#! python3
import ctypes
import struct
import collections


class FlashSector(ctypes.Structure):
    _fields_ = [
        ('szSector',        ctypes.c_uint32),
        ('AddrSector',      ctypes.c_uint32)
    ]

class FlashDevice(ctypes.Structure):
    _fields_ = [
        ('Vers',            ctypes.c_uint16),
        ('DevName',         ctypes.c_char * 128),
        ('DevType',         ctypes.c_uint16),
        ('DevAdr',          ctypes.c_uint32),
        ('szDev',           ctypes.c_uint32),
        ('szPage',          ctypes.c_uint32),
        ('Res',             ctypes.c_uint32),
        ('valEmpty',        ctypes.c_uint8),
        ('toProg',          ctypes.c_uint32),
        ('toErase',         ctypes.c_uint32),
        ('sectors',         FlashSector * 512)
    ]


Symbol = collections.namedtuple('Symbol', 'name addr size')


class FlashAlgo(object):
    ''' Flash Programming Algorithm parsed from MDK FLM file '''

    ''' 中断 halt 程序，让函数执行完后返回到这里来执行从而让 CPU 自动 halt 住 '''
    ALGO_HEADER_ARM = [0xE00ABE00, 0x062D780D, 0x24084068, 0xD3000040, 0x1E644058, 0x1C49D1FA, 0x2A001E52, 0x4770D1F2]
    ALGO_HEADER_RV =  [0x00100073, 0x00088893, 0x00088893, 0x00088893, 0x00088893, 0x00088893, 0x00088893, 0x00088893]
    SIZE_HEADER = len(ALGO_HEADER_ARM) * 4

    def __init__(self, path, ram_start, ram_size: 'size of RAM for Algorithm'):
        self.flash_algo = {}

        try:
            from elftools.elf.elffile import ELFFile
            self.elf = ELFFile(open(path, 'rb'))

            self.flash_algo['arch'] = self.elf.get_machine_arch()

            self.syms = {}
            for sym in self.elf.get_section_by_name('.symtab').iter_symbols():
                self.syms[sym.name] = Symbol(sym.name, sym.entry['st_value'], sym.entry['st_size'])

            for func in ('Init', 'UnInit', 'EraseChip', 'EraseSector', 'ProgramPage', 'Verify', 'BlankCheck', 'Read'):
                if func in self.syms:
                    self.flash_algo[f'pc_{func}'] = self.syms[func].addr + ram_start + self.SIZE_HEADER
                else:
                    self.flash_algo[f'pc_{func}'] = 0xFFFFFFFF

            self.FlashInfo()

            self.parseAlgo()

            self.flash_algo['load_address'] = ram_start
            self.flash_algo['static_base']  = ram_start + self.SIZE_HEADER + self.ro_size
            self.flash_algo['begin_data']   = ram_start + self.SIZE_HEADER + self.ro_size + self.rw_size + self.zi_size
            self.flash_algo['begin_stack']  = ram_start + ram_size

            algo_word = struct.unpack('<' + 'L' * (len(self.algo_data) // 4), self.algo_data)

            if self.flash_algo['arch'] == 'ARM':
                self.flash_algo['instructions'] = self.ALGO_HEADER_ARM + [word for word in algo_word]

            elif self.flash_algo['arch'] == 'RISC-V':
                self.flash_algo['instructions'] = self.ALGO_HEADER_RV  + [word for word in algo_word]

        except Exception as e:
            print(f'parse elf file fail: {e}')

    def FlashInfo(self):
        ''' parse Flash Info defined in struct FlashDevice, that defined in file FlashDev.c '''
        fldev = self.syms['FlashDevice']

        fldev = FlashDevice.from_buffer_copy(self.read(fldev.addr, fldev.size))
        
        self.flash_algo['flash_start']      = fldev.DevAdr
        self.flash_algo['flash_size']       = fldev.szDev
        self.flash_algo['flash_page_size']  = fldev.szPage

        self.flash_algo['sector_sizes'] = []
        for sector in fldev.sectors:
            if (sector.AddrSector, sector.szSector) == (0xFFFFFFFF, 0xFFFFFFFF):
                break
            
            self.flash_algo['sector_sizes'].append((sector.AddrSector, sector.szSector))

    def parseAlgo(self):
        for section in self.elf.iter_sections():
            name_and_type = (section.name, section['sh_type'])
            if   name_and_type == ('PrgCode', 'SHT_PROGBITS'):
                s_ro = section
            elif name_and_type == ('PrgData', 'SHT_PROGBITS'):
                s_rw = section
            elif name_and_type == ('PrgData', 'SHT_NOBITS'):
                s_zi = section

        ''' 若 zi 段丢失，创建一个空的 '''
        if s_rw is not None and 's_zi' not in locals():
            s_zi = {
                'sh_addr': s_rw['sh_addr'] + s_rw['sh_size'],
                'sh_size': 0
            }
        
        self.ro_start = s_ro['sh_addr']
        self.ro_size  = s_ro['sh_size']
        self.rw_start = s_rw['sh_addr']
        self.rw_size  = s_rw['sh_size']
        self.zi_start = s_zi['sh_addr']
        self.zi_size  = s_zi['sh_size']

        self.algo_data = bytearray(self.ro_size + self.rw_size + self.zi_size)
        for section in (s_ro, s_rw):
            start, size = section['sh_addr'], section['sh_size']
            
            self.algo_data[start : start + size] = section.data()

    def read(self, addr, size):
        ''' read segment data from elf file '''
        for segment in self.elf.iter_segments():
            seg_addr = segment['p_paddr']
            seg_size = min(segment['p_memsz'], segment['p_filesz'])
            
            if addr >= seg_addr and addr + size <= seg_addr + seg_size:
                start = addr - seg_addr
                return segment.data()[start : start+size]
            else:
                continue



if __name__ == '__main__':
    falgo = FlashAlgo('../FlashAlgo/STM32F10x_128.FLM', 0x20000000, 0x1000)
    for key, val in falgo.flash_algo.items():
        print(f'{key:16s}:', end='')
        if isinstance(val, int):
            print(f'{val:08X}')
        elif key == 'instructions':
            for i, x in enumerate(val):
                if i%8 == 0:
                    print()
                print(f'{x:08X}, ', end='')
        else:
            print(val)
