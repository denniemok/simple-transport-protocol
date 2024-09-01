#!/usr/bin/env python3

# Usage: python3 receiver.py receiver_port sender_port file_received.txt flp rlp
# Author: Dennie Mok


import datetime, time  # to calculate the time delta of packet transmission
import logging, sys  # to write the log
import socket  # to send packet via UDP socket
from threading import Thread  # to manage threads
from random import seed, randrange  # to randomise values
import struct  # to encode or decode bytes


class Receiver:
    
    
    # receiver constructor
    def __init__(self, receiver_port: int, sender_port: int, filename: str, flp: float, rlp: float) -> None:
        
        #:param receiver_port: UDP port number to be used by the receiver to receive PTP segments from the sender.
        #:param sender_port: the UDP port number to be used by the sender to send PTP segments to the receiver.
        #:param filename: the name of the text file into which the text sent by the sender should be stored
        #:param flp: forward loss probability, which is the probability that any segment in the forward direction (Data, FIN, SYN) is lost.
        #:param rlp: reverse loss probability, which is the probability of a segment in the reverse direction (i.e., ACKs) being lost.
        
        self.receiver_port = int(receiver_port)
        self.sender_port = int(sender_port)

        # assume only localhost is used
        self.receiver_address = ("127.0.0.1", self.receiver_port)
        self.sender_address = ("127.0.0.1", self.sender_port)

        self.filename = str(filename)
        self.flp = int(float(flp) * 100)  # convert to 100%
        self.rlp = int(float(rlp) * 100)  # convert to 100%

        # buffer
        self.buffer = {}  # dictionary for managing received data
        # use dictionary so our receive buffer can hold variable length of data

        # seqno constant
        self.DSN = 0  # first data seqno

        # state variables
        self.established = False
        self.n_fin = 0

        # initial timestamp
        self.itstamp = 0

        # stats variables
        self.dupdata = 0
        self.drpack = 0
        self.drpdata = 0
        self.lendata = 0

        # init UDP socket
        print(f"{datetime.datetime.now()}\tIP Address: {self.receiver_address}")
        self.receiver_socket = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
        self.receiver_socket.bind(self.receiver_address)

        # start the listener sub-thread
        self.active = True  # control termination of program
        listener_thread = Thread(target=self.fw_listener, daemon=True)
        listener_thread.start()


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
            self.itstamp = time.time() * 1000
            return 0

        return round(time.time() * 1000 - self.itstamp, 2)


    # segment sending utility
    def send_msg(self, flag, seqno):
        
        # expected segments: ACK = 1, RESET = 4
        # server will never send segments other than ACKs and RESETs
        # always prioritise logging due to time sentitivity concern 
        
        seed()

        # implement reverse loss probability function
        if flag != 4 and randrange(100) < self.rlp:
            logging.info(f"drp\t{self.get_time()}\t{self.get_type(flag)}\t{seqno}\t0")
            print(f"{datetime.datetime.now()}\tdrp | type: {flag} | seqno: {seqno} | size: 0")
            self.drpack += 1
            return

        # compose 4 bytes packet, no payload is sent
        logging.info(f"snd\t{self.get_time()}\t{self.get_type(flag)}\t{seqno}\t0")
        print(f"{datetime.datetime.now()}\tsnd | type: {flag} | seqno: {seqno} | size: 0")

        self.receiver_socket.sendto(struct.pack('2H', flag, seqno), self.sender_address)


    # sub-thread forward listener
    # independent of main thread
    # listen for response from sender
    def fw_listener(self):
        
        # expected segments: DATA = 0, SYN = 2, FIN = 3, RESET = 4
        # sender will not receive ACKs

        print(f"{datetime.datetime.now()}\t✓ LISTEN")
        
        while self.active:
            
            # try to receive any incoming message from the sender
            incoming_message, _ = self.receiver_socket.recvfrom(1004)  # MSS + 4-byte header
            
            # decode message
            stp_type = int(struct.unpack('2H', incoming_message[0:4])[0])
            stp_seqno = int(struct.unpack('2H', incoming_message[0:4])[1])
            stp_size = len(incoming_message[4:])

            # if SYN (type = 2) is received
            # reset timestamp until it is well received
            if stp_type == 2: 
                self.itstamp = 0

            seed()

            # implement forward loss probability function
            if stp_type != 4 and randrange(100) < self.flp:
                logging.info(f"drp\t{self.get_time()}\t{self.get_type(stp_type)}\t{stp_seqno}\t{stp_size}")
                print(f"{datetime.datetime.now()}\tdrp | type: {stp_type} | seqno: {stp_seqno} | size: {stp_size}")
                if stp_type == 0:
                    self.drpdata += 1
                continue

            # print out decoded message
            logging.info(f"rcv\t{self.get_time()}\t{self.get_type(stp_type)}\t{stp_seqno}\t{stp_size}")
            print(f"{datetime.datetime.now()}\trcv | type: {stp_type} | seqno: {stp_seqno} | size: {stp_size}")

            # if RESET (type = 4) is received
            if stp_type == 4:
                if not self.established:
                    print(f"{datetime.datetime.now()}\t✗ ESTAB")
                else:
                    print(f"{datetime.datetime.now()}\t✗ FINISH")
                self.active = False  # terminate
                break

            # In ESTAB phase ...
            if not self.established:
                
                # if SYN (type = 2) is received
                if stp_type == 2: 

                    self.established = True  # change state

                    # compute first data sequence number
                    self.DSN = (stp_seqno + 1) % (2**16)

                    # send ACK
                    self.send_msg(1, self.DSN)

                    print(f"{datetime.datetime.now()}\t✓ ESTAB")

                # otherwise
                else:

                    print(f"{datetime.datetime.now()}\t✗ ESTAB")
                    self.reset()
                    break

            # After ESTAB phase ...
            elif self.established:
                
                # if DATA (type = 0) is received
                if stp_type == 0:

                    # if received data frame during FIN phase
                    if self.n_fin != 0:
                        print(f"{datetime.datetime.now()}\t✗ FINISH")
                        self.reset()
                        break

                    pos = 0  # pos of pkt in which the sender sends

                    # compute pos of pkt in which the sender sends
                    # cycle back to 0 whenever seqno go beyond 2**16 - 1
                    # best algorithm I have come up with :)
                    # can support many cycles
                    for i in range(0, 15):  # support up to 983 KB file
                        if (stp_seqno + 65536*i - self.DSN) % 1000 == 0:
                            pos = int((stp_seqno + 65536*i - self.DSN) / 1000)
                            break
                    
                    print(f"{datetime.datetime.now()}\t↑ pkt #{pos}")

                    # if pkt not in buffer
                    if pos not in self.buffer.keys():
                        self.buffer[pos] = incoming_message[4:]
                        self.lendata += len(self.buffer[pos])
                    
                    # otherwise
                    else:
                        print(f"{datetime.datetime.now()}\t... duplicated data segment ...")
                        self.dupdata += 1

                    # compute cumulative ACK
                    cumu_seqno = self.DSN

                    for i in range(0, 1000):
                        if i not in self.buffer.keys():
                            break
                        else:
                            cumu_seqno += len(self.buffer[i])

                    cumu_seqno = cumu_seqno % (2**16)

                    # send ACK
                    self.send_msg(1, cumu_seqno)

                # if FIN (type = 3) is received
                elif stp_type == 3:

                    self.n_fin += 1

                    # send ACK
                    self.send_msg(1, (stp_seqno + 1) % (2**16))

                    # start timer in subthread, only at its first reception
                    if self.n_fin == 1:
                        a = Thread(target=self.timed_close, daemon=True)
                        a.start()

                # otherwise
                else:

                    print(f"{datetime.datetime.now()}\t✗ FINISH")
                    self.reset()
                    break


    # timed close
    # handled in sub-thread
    def timed_close(self):
        
        # wait 2*MSL where MSL = 1 second
        time.sleep(2) 
        
        print(f"{datetime.datetime.now()}\t✓ FINISH")

        # terminate all sub-threads
        self.active = False


    # send RESET and close
    def reset(self):
        
        # send RESET
        self.send_msg(4, 0)

        # terminate all sub-threads
        self.active = False


    # main thread
    def run(self):
        
        # main loop to hold main thread
        while self.active:
            pass

        # output result string, regardless of success
        # maintain output even when transmission is not completed
        scope = sorted(self.buffer.keys())
        result = b""
        for i in scope:
            if i != 0 and i-1 not in scope:
                break
            result += self.buffer[i]

        # write to file
        with open(self.filename, 'w') as f:
            f.write(result.decode('utf-8')) # only decode the string once it's fully composed

        print(f"{datetime.datetime.now()}\tReceived Segments: {sorted(self.buffer.keys())}")
        
        # write stats to log
        logging.info(f"Data Received: {self.lendata} bytes")
        logging.info(f"Data Segments Received: {len(self.buffer.keys())}")
        logging.info(f"Duplicate Data Segments Received: {self.dupdata}")
        logging.info(f"Data Segments Dropped: {self.drpdata}")
        logging.info(f"ACK Segments Dropped: {self.drpack}")

        print(f"{datetime.datetime.now()}\t✓ CLOSE")

        # close socket
        self.receiver_socket.close()


# main program
if __name__ == '__main__':
    
    # log written to Sender_log.txt
    logging.basicConfig(
        filename="output/receiver_log.txt",
        filemode="w",
        level=logging.INFO,
        format='%(message)s')

    # check arguments
    if len(sys.argv) != 6:
        print("\nIncorrect Argument Length")
        print("Syntax: python3 receiver.py <receiver_port> <sender_port> <file_received.txt> <flp> <rlp>")
        print("<flp>: forward loss probability a float value between 0 and 1")
        print("<rlp>: reverse loss probability, a float value between 0 and 1\n")
        exit(0)

    # create receiver object
    receiver = Receiver(*sys.argv[1:])
    # run main thread
    receiver.run()