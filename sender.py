#!/usr/bin/env python3

# Usage: python3 sender.py sender_port receiver_port file_to_send.txt max_win rto
# Author: Dennie Mok


import datetime, time  # to calculate the time delta of packet transmission
import logging, sys  # to write the log
import socket  # to send packet via UDP socket
from threading import Thread  # to manage threads
from random import seed, randrange  # to randomise values
import struct  # to encode or decode bytes


class Sender:


    # sender constructor
    def __init__(self, sender_port: int, receiver_port: int, filename: str, max_win: int, rto: int) -> None:
                
        # :param sender_port: the UDP port number to be used by the sender to send PTP segments to the receiver
        # :param receiver_port: the UDP port number on which receiver is expecting to receive PTP segments from the sender
        # :param filename: the name of the text file that must be transferred from sender to receiver using your reliable transport protocol.
        # :param max_win: the maximum window size in bytes for the sender window.
        # :param rto: the value of the retransmission timer in milliseconds. This should be an unsigned integer.
        
        self.sender_port = int(sender_port)
        self.receiver_port = int(receiver_port)

        # assume only localhost is used
        self.sender_address = ("127.0.0.1", self.sender_port)
        self.receiver_address = ("127.0.0.1", self.receiver_port)

        self.filename = str(filename)
        self.win_size = int(int(max_win) / 1000)  # in terms of MSS
        self.rto = int(rto) / 1000  # in terms of seconds

        # arrays and buffers
        self.buffer = []  # array for managing data segment payload (index: pkt id)
        self.segmtRcved = []  # array for manging segment received status (index: pkt id)
        self.segmtSent = []  # array for managing segment sent status (index: pkt id)
        self.segmtTimer = []  # array for managing timer trigger status (index: pkt id)
        self.segmtAck = {}  # dictionary for managing triple dup ack (index: pkt id)

        # data constants
        self.buffer_size = 0 # number of partitions
        self.data_size = 0 # number of bytes

        # state variables
        self.established = False
        self.sendingdata = False
        self.finished = False
        self.terminate = False # for reset

        # window boundaries
        self.win_lb = 0  # lower bound, in terms of pkt id
        self.win_ub = 0  # upper bound, in terms of pkt id

        # initial timestamp
        self.itstamp = 0  # pivot point for logging

        # stats variables
        self.retransmit = 0
        self.byteSent = 0
        self.dupAck = 0

        # seqno constants
        seed()
        self.ISN = randrange(0,2**16) # initial seqno        
        self.DSN = (self.ISN + 1) % (2**16) # first data seqno
        self.FSN = 0 # first fin seqno
        self.PSN = 0 # previous seqno

        # init UDP socket
        print(f"{datetime.datetime.now()}\tIP Address: {self.sender_address}")
        self.sender_socket = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
        self.sender_socket.bind(self.sender_address)

        # start the listener sub-thread
        self.active = True  # control termination of program
        listener_thread = Thread(target=self.rv_listener, daemon=True)
        listener_thread.start()


    # timed FIN executor
    # handled in sub-thread, called recursively until the 4th retrials
    def fin_exec(self, i):  # i is used for managing recusion loop (recommend init to 1)

        # send FIN
        self.send_msg(3, self.FSN, 0)
        # set timer
        time.sleep(self.rto)
        
        # retrial
        if not self.finished:
            
            # fast termination
            if self.terminate or not self.active:
                return
            
            # recursive base
            # give up at 4th retrial
            if i == 4:
                self.terminate = True
                return

            # retransmit SYN
            print(f"{datetime.datetime.now()}\tFIN Retrial {i}")
            
            # recursive call
            self.fin_exec(i+1) 


    # main FIN handler
    # handled in main thread, listen for control signals from sub-threads
    def fin_hdlr(self):
        
        # setup timed sub-thread
        d = Thread(target=self.fin_exec, args=(1,), daemon=True)
        d.start()

        # state listener
        # loop until connection is finished
        while not self.finished:

            if self.terminate:
                print(f"{datetime.datetime.now()}\t✗ FINISH")
                self.reset()
                break
            
            elif not self.active:
                print(f"{datetime.datetime.now()}\t✗ FINISH")
                self.close()
                break

        print(f"{datetime.datetime.now()}\t✓ FINISH")


    # timed ESTAB executor
    # handled in sub-thread, called recursively until the 4th retrials
    def estab_exec(self, i): # i is used for managing recusion loop (recommend init to 1)

        # send SYN
        self.send_msg(2, self.ISN, 0)
        # set timer
        time.sleep(self.rto)
        
        # retrial
        if not self.established:
            
            # fast termination
            if self.terminate or not self.active:
                return
            
            # recursive call
            # give up at 4th retrial
            if i == 4:
                self.terminate = True
                return

            # retransmit SYN
            print(f"{datetime.datetime.now()}\tESTAB Retrial {i}")
            
            # recursive call
            self.estab_exec(i+1)


    # main ESTAB handler
    # handled in main thread, listen for control signals from sub-threads
    def estab_hdlr(self): 

        print(f"{datetime.datetime.now()}\t")
        print(f"{datetime.datetime.now()}\tISN: {self.ISN}")
        
        # setup timed sub-thread
        a = Thread(target=self.estab_exec, args=(1,), daemon=True)
        a.start()

        # state listener
        # loop until connection is established
        while not self.established:
            
            if self.terminate:
                print(f"{datetime.datetime.now()}\t✗ ESTAB")
                self.reset()
                break
            
            elif not self.active:
                print(f"{datetime.datetime.now()}\t✗ ESTAB")
                self.close()
                break

        print(f"{datetime.datetime.now()}\t✓ ESTAB")


    # timed segment retransmission executor
    # handled in sub-thread, called recursively until the 4th retrials
    # data is retransmitted unlimitedly until it is received by server
    def retranseg_exec(self, i):  # i denotes pkt id
        
        # set timer
        time.sleep(self.rto)

        # fast termination
        if self.terminate or not self.active:
            return
        
        # if segment is not yet received
        if self.segmtRcved[i] == 0:
            
            # restransmit segment
            print(f"{datetime.datetime.now()}\tretransmit pkt #{i}")
            print(f"{datetime.datetime.now()}\ttimer set for #{i}")
            self.retransmit += 1
            
            self.send_msg(0, (self.DSN + 1000 * i) % (2**16), i)
            
            # recursive call, no recursive base is supplied
            # can possibly run in infinite loop
            self.retranseg_exec(i)


    # main segment transmission handler and executor
    # send segments in batch (i.e., pipelined manner)
    # follows the sliding window mechanism
    # handled in main thread, listen for control signals from sub-threads
    # since it is a hybrid of executor and handler, it may introduce overhead in processing transmission
    def transeg_exec_hdlr(self): 

        print(f"{datetime.datetime.now()}\tinitial window: {self.win_lb} - {self.win_ub}")
        
        # state listener
        # loop until end of data transmission
        while self.sendingdata:

            if self.terminate:
                print(f"{datetime.datetime.now()}\t✗ DATA")
                self.reset()
                break

            elif not self.active:
                print(f"{datetime.datetime.now()}\t✗ DATA")
                self.close()
                break

            # avoid window boundaries from dynamically changing
            # execute loop send in static manner
            lb = self.win_lb
            ub = self.win_ub

            # executor
            # loop send segments within sending window
            for i in range(lb, ub+1):

                # fast termination
                if self.terminate or not self.active:
                    break
                
                # if data is not sent
                if self.segmtSent[i] == 0:
                    
                    # marked as sent
                    self.segmtSent[i] = 1
                    self.byteSent += len(self.buffer[i])
                    
                    # check if it is the oldest packet in the sending window
                    if i == lb:

                        # if so, set timer
                        self.segmtTimer[i] = 1
                        
                        # start timed sub-thread
                        print(f"{datetime.datetime.now()}\ttimer set for pkt #{i}")
                        b = Thread(target=self.retranseg_exec, args=(i,), daemon=True)
                        b.start()

                    # send data segment
                    self.send_msg(0, (self.DSN + 1000 * i) % (2**16), i)

                # if data is sent already, and now it becomes the oldest packet in the window
                # and if no timer is already set for that packet
                # this maintains a timer for all the oldest unacked packet in the window
                elif i == lb and self.segmtTimer[i] == 0:
                        
                        # set timer
                        self.segmtTimer[i] = 1

                        # start timer thread
                        print(f"{datetime.datetime.now()}\ttimer set for pkt #{i}")
                        c = Thread(target=self.retranseg_exec, args=(i,), daemon=True)
                        c.start()

        print(f"{datetime.datetime.now()}\t✓ DATA")


    # sub-thread reverse listener
    # independent of main thread
    # listen for response from receiver
    def rv_listener(self):
        
        # expected segments: ACK = 1, RESET = 4
        # sender will not receive packets other than ACK or RESET

        print(f"{datetime.datetime.now()}\t✓ LISTEN")
        
        while self.active:

            # this is triggered when a message is received
            # expect 4 bytes message since receiver does not send data
            incoming_message, _ = self.sender_socket.recvfrom(4)  # 4-byte header
            
            # decode segment
            stp_type = int(struct.unpack('2H', incoming_message[0:4])[0])
            stp_seqno = int(struct.unpack('2H', incoming_message[0:4])[1])

            logging.info(f"rcv\t{self.get_time()}\t{self.get_type(stp_type)}\t{stp_seqno}\t0")
            print(f"{datetime.datetime.now()}\trcv | type: {stp_type} | seqno: {stp_seqno} | size: 0")

            # if RESET (type = 4) is received
            if stp_type == 4:
                self.active = False  # terminate program
                break
            
            # In ESTAB phase ...
            if not self.established:
                
                # if ACK (type = 1) is received with correct seqno
                if stp_type == 1 and stp_seqno == self.DSN:
                    self.established = True  # change state
                    self.sendingdata = True 

                # otherwise
                else:
                    self.terminate = True  # send RESET
                    break

            # After setting up the connection, we begin sending data
            # In Data Transmission phase ...
            elif self.sendingdata:
                
                # if ACK (type = 1) is received
                if stp_type == 1:
                
                    pos = 0  # pos of pkt in which the receiver requests

                    # compute which pkt receiver wants
                    # cycle back to 0 whenever seqno go beyond 2**16 - 1
                    # best algorithm I have come up with :)
                    # can support many cycles
                    for i in range(0, 15):  # support up to 983 KB file
                        if (stp_seqno + 65536*i - self.DSN) % 1000 == 0:
                            pos = int((stp_seqno + 65536*i - self.DSN) / 1000)
                            break
                        elif stp_seqno + 65536*i - self.DSN == self.data_size:
                            pos = self.buffer_size  # in case of last data segment not filling up 1000 bytes
                            break

                    # clean up dup ack buffer
                    if self.PSN != stp_seqno:
                        self.segmtAck = {}
                    
                    # main data ACK handler, decide actions to which ACK is received
                    # designed to deal with cumulative ACK, assume everything before ACK is received
                    # if receiver wants back the oldest pkt in window, that means pkt win_lb is lost
                    # it must be a dup ack because win_lb becomes the lower bound due to sliding window
                    if pos == self.win_lb:  # oldest packet in sending window

                        print(f"{datetime.datetime.now()}\t... dup ack for pkt #{pos} ...")

                        self.dupAck += 1
                        
                        # manage for triple dup ack
                        if self.PSN == stp_seqno: # if previous seqno = current seqno
                            if pos not in self.segmtAck.keys():
                                self.segmtAck[pos] = 1
                            else:
                                self.segmtAck[pos] += 1
                                
                                # trigger fast retransmition on triple dup ack
                                if self.segmtAck[pos] % 3 == 0:
                                   
                                    print(f"{datetime.datetime.now()}\t... fast retransmit pkt #{pos} ...")
                                    self.send_msg(0, (self.DSN + 1000 * pos) % (2**16), pos)
                                    self.retransmit += 1
                    
                    # if receiver wants latter segment
                    # that means everything before pos is received
                    elif pos > self.win_lb:
                        
                        # flag as ACKed for everything before ACKed seqno
                        for j in range(0, pos):
                            self.segmtRcved[j] = 1
                        
                        # when pos goes beyond the data window
                        # that means we have finished sending all the data
                        if pos == self.buffer_size:
                            
                            self.sendingdata = False  # change state
                            self.FSN = stp_seqno
                            
                            print(f"{datetime.datetime.now()}\tsegmtSent: {self.segmtSent}")
                            print(f"{datetime.datetime.now()}\tsegmtRcved: {self.segmtRcved}")
                            print(f"{datetime.datetime.now()}\tsegmtTimer: {self.segmtTimer}")
                        
                        # slide the window if we have not finished sending all the data
                        else:
                            self.win_ub = min(pos + self.win_size - 1, self.buffer_size - 1)
                            self.win_lb = pos
                            print(f"{datetime.datetime.now()}\t↑ window slided to {self.win_lb} - {self.win_ub}")

                    # unexpected ACK when pos < win_lb
                    # if receiver wants something that is smaller than the current window lower bound
                    # it must be an error or premature delay, since window has already slided
                    else: 
                        print(f"{datetime.datetime.now()}\t... unexpected low ack ....")
                        # which might still happen, but rarely
                        # no action is taken because higher cumulative ACK has already received
                
                # otherwise
                else:
                    self.terminate = True  # send RESET
                    break

            # After finished sending all the data, we enter the finish phase
            # In Finish phase ...
            else:
                
                # if ACK (type = 1) is received with correct seqno
                if stp_type == 1 and stp_seqno == (self.FSN + 1) % (2**16):
                    self.finished = True  # change state
                
                # otherwise
                else:
                    self.terminate = True  # send RESET
                    break

            # store current seqno as previous seqno for dup ack computation
            self.PSN = stp_seqno


    # read file and import data
    def read_file(self):

        # read file
        with open(self.filename, 'r') as f:
            data = f.read().encode('utf-8') # encode file content to bytes

        # record size of data
        self.data_size = len(data)
        
        print(f"{datetime.datetime.now()}\t")
        print(f"{datetime.datetime.now()}\t{self.filename} has {self.data_size} bytes of data")

        # compute partition of split per 1000 bytes of data
        self.buffer_size = int(self.data_size / 1000)

        if self.data_size % 1000 != 0:
            self.buffer_size += 1

        print(f"{datetime.datetime.now()}\t{self.buffer_size} paritions, each holds at most 1000 bytes")
        print(f"{datetime.datetime.now()}\t✓ READ")
        print(f"{datetime.datetime.now()}\t")

        # load data into buffer
        for i in range(0, self.buffer_size):
            if i == self.buffer_size - 1:
                self.buffer.append(data[i*1000:len(data)])
            else: 
                self.buffer.append(data[i*1000:(i+1)*1000])

        # init auxillary buffers
        self.segmtRcved = [0] * self.buffer_size
        self.segmtSent = [0] * self.buffer_size
        self.segmtTimer = [0] * self.buffer_size

        # setup initial window boundaries
        self.win_ub = min(self.win_size - 1, self.buffer_size - 1)


    # segment patching and sending utility
    def send_msg(self, flag, seqno, i):
        
        # expected segments: DATA = 0, SYN = 2, FIN = 3, RESET = 4
        # sender will never send ACKs
        # always prioritise logging due to time sentitivity concern 

        # send non-data segment (with flag != 0)
        if flag != 0:
            
            logging.info(f"snd\t{self.get_time()}\t{self.get_type(flag)}\t{seqno}\t0")
            print(f"{datetime.datetime.now()}\tsnd | type: {flag} | seqno: {seqno} | size: 0")
            
            self.sender_socket.sendto(struct.pack('2H', flag, seqno), self.receiver_address)
        
        # send data segment (with flag = 0)
        else:

            logging.info(f"snd\t{self.get_time()}\t{self.get_type(flag)}\t{seqno}\t{len(self.buffer[i])}")
            print(f"{datetime.datetime.now()}\tsnd | type: {flag} | seqno: {seqno} | size: {len(self.buffer[i])}")

            self.sender_socket.sendto(struct.pack('2H', flag, seqno) + self.buffer[i], self.receiver_address)


    # segment type decoding utility
    # convert stp type from int to str
    def get_type(self, i):
        
        if i == 0:
            return "DATA"
        elif i == 1:
            return "ACK"
        elif i == 2:
            return "SYN"
        elif i == 3:
            return "FIN"
        else:
            return "RESET"


    # timestamp utility for logging
    def get_time(self):
        
        if self.itstamp == 0:
            self.itstamp = time.time() * 1000  # adapt to milliseconds
            return 0

        return round(time.time()*1000 - self.itstamp, 2)


    # send RESET and close
    def reset(self):
        
        # send RESET msg
        self.send_msg(4, 0, 0)
        # close 
        self.close()


    # close connection
    def close(self):
        
        # terminate all sub-threads and their loops
        self.active = False

        # write stats to log
        logging.info(f"Data Transferred: {self.byteSent} bytes")
        logging.info(f"Data Segments Sent: {self.segmtSent.count(1)}")
        logging.info(f"Retransmitted Data Segments: {self.retransmit}")
        logging.info(f"Duplicate Acknowledgements: {self.dupAck}")

        print(f"{datetime.datetime.now()}\t✓ CLOSE")

        # close socket
        # self.sender_socket.close()
        
        # exit program, just in case
        exit(0)


    # main thread
    # major sequence of actions to be handled in the main thread
    def run(self):

        # handler: listen to control signals from sub-threads
        # executor: execute timed actions in sub-threads, summoned by handlers
        
        self.estab_hdlr() # manage establishment phase
        self.read_file() # read files and init critical variables
        self.transeg_exec_hdlr() # manage data sending phase
        self.fin_hdlr() # manage finish phase
        self.close() # manage close phase


# main program
if __name__ == '__main__':
    
    # log written to Sender_log.txt
    logging.basicConfig(
        filename="output/sender_log.txt",
        filemode="w",
        level=logging.INFO,
        format='%(message)s')

    # check arguments
    if len(sys.argv) != 6:
        print("\nIncorrect Argument Length")
        print("Syntax: python3 sender.py <sender_port> <receiver_port> <file_to_send.txt> <max_win> <rto>")
        print("<max_win>: Size of the sending window in multiples of 1000 (MSS = 1000 bytes)")
        print("<rto>: Timeout value in milliseconds for the oldest packet in the sending window\n")
        exit(0)

    # create sender object
    sender = Sender(*sys.argv[1:])
    # run main thread
    sender.run()