import threading
from utils import int_to_hex

class NikobusApi:
    def __init__(self, log, nikobus):
        self.log = log
        self.log('NikobusApi() enter', 5)
        self.nikobus = nikobus
        self.log('NikobusApi() leave', 9)

    def send_command(self, command, callback):
        self.log('NikobusApi.sendCommand() enter', 5)
        self.log('command = ' + command, 7)
        self.nikobus.send_command(command,
            lambda err: self.send_command_callback(err, callback)
        )
        self.log('NikobusApi.sendCommand() leave', 9)

    def send_command_callback(self, err, callback):
        self.log('Nikobus.sendCommand() callback (err = ' + str(err) + ')', 1 if err else 8)
        callback(err)

    def get_output_state(self, address, group, timeout, callback):
        self.log('NikobusApi.getOutputState() enter', 5)
        self.log('address = ' + int_to_hex(address, 4) + ', ' + 'group = ' + str(group) + ', ' + 'timeout = ' + str(timeout), 7)
        cmd = ''
        if group == 1:
            cmd = self.make_pc_link_command(0x12, address) # $1C12B602xxxxxx
        elif group == 2:
            cmd = self.make_pc_link_command(0x17, address) # $1C17B602xxxxxx
        self.nikobus.send_command_get_answer(cmd, timeout,
            lambda err, answer: self.get_output_state_callback(err, answer, callback)
        )
        self.log('NikobusApi.getOutputState() leave', 9)

    def get_output_state_callback(self, err, answer, callback):
        self.log('Nikobus.sendCommandGetAnswer() callback (err = ' + str(err) + ', answer = ' + str(answer) + ')', 1 if err else 8)
        if err:
            callback(err, None)
            return
        if len(answer) != 1 + 2 + 4 + 14 + 4 + 2:
            callback(ValueError('unexpected answer length (' + str(len(answer)) + ')'), None)
            return
        # Other checks and processing here
        callback(None, answer)

    def set_output_state(self, address, group, value, timeout, callback):
        self.log('NikobusApi.setOutputState() enter', 5)
        self.log('address = ' + int_to_hex(address, 4) + ', ' + 'group = ' + str(group) + ', ' + 'value = ' + str(value) + ', ' + 'timeout = ' + str(timeout), 7)
        cmd = ''
        if group == 1:
            cmd = self.make_pc_link_command(0x15, address, value + 'FF') # $1E15B602xxxxxxxxxxxxFFxxxxxx
        elif group == 2:
            cmd = self.make_pc_link_command(0x16, address, value + 'FF') # $1E16B602xxxxxxxxxxxxFFxxxxxx
        self.nikobus.send_command_get_answer(cmd, timeout,
            lambda err, answer: self.set_output_state_callback(err, answer, callback)
        )
        self.log('NikobusApi.setOutputState() leave', 9)

    def set_output_state_callback(self, err, answer, callback):
        self.log('Nikobus.sendCommandGetAnswer() callback (err = ' + str(err) + ', answer = ' + str(answer) + ')', 1 if err else 8)
        if err:
            callback(err, None)
            return
        if len(answer) != 1 + 2 + 8 + 2:
            callback(ValueError('unexpected answer length (' + str(len(answer)) + ')'), None)
            return
        # Other checks and processing here
        callback(None, answer)

    def make_pc_link_command(self, func, addr, args=None):
        data = int_to_hex(func, 2) + int_to_hex((addr >> 0) & 0xFF, 2) + int_to_hex((addr >> 8) & 0xFF, 2)
        if args is not None:
            data += args
        return self.append_crc2('$' + int_to_hex(len(data) + 10, 2) + self.append_crc1(data))

    def append_crc1(self, data):
        crc = self.calc_crc1(data)
        return data + int_to_hex(crc, 4)

    def append_crc2(self, data):
        crc = self.calc_crc2(data)
        return data + int_to_hex(crc, 2)

    def calc_crc1(self, data):
        crc = 0xFFFF
        for j in range(0, len(data) // 2):
            crc = crc ^ (int(data[j * 2:j * 2 + 2], 16) << 8)
            for i in range(0, 8):
                if (crc >> 15) & 1 != 0:
                    crc = (crc << 1) ^ 0x1021
                else:
                    crc = crc << 1
        return crc & 0xFFFF

    def calc_crc2(self, data):
        crc = 0
        for i in range(len(data)):
            crc = crc ^ ord(data[i])
            for j in range(0, 8):
                if (crc & 0xFF) >> 7 != 0:
                    crc = crc << 1
                    crc = crc ^ 0x99
                else:
                    crc = crc << 1
        return crc & 0xFF
