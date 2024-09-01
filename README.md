# Simple Transport Protocol

Simple Transport Protocol (STP) is a streamlined version of TCP designed for reliable, uni-directional data transfer. This project implements STP on top of UDP sockets, featuring a sender and receiver for text file delivery. Additionally, an unreliable channel is incorporated to the receiver, allowing segments to be dropped in both directios to emulate packet loss scenarios.

## Segment Structure

```
|<-------------------------- UDP Segment ----------------------------->|
| UDP Header | Type (2 bytes) | SeqNo (2 bytes) | Data (0 - MSS bytes) |
             |<---------- STP Header ---------->|
             |<---------------------- STP Segment -------------------->|
```

- Type: 5 possible values: DATA = 0, ACK = 1, SYN = 2, FIN = 3, RESET = 4 (only one type is allowed per segment)
- SeqNo: Ranges from 0 to 2^16 - 1 and cycles back to 0 after reaching the maximum value.
  - For DATA segments, the sequence number increases by the size (in bytes) of each segment.
  - For ACK segments, the sequence number acts as a cumulative acknowledgment, indicating the number of the next byte expected by the receiver.
  - For SYN segments, the sequence number is the Initial Sequence Number (ISN), which is a randomly chosen integer between 0 and 2^16 - 1. The sequence number of the first DATA segment in the connection should be ISN + 1.
  - For FIN packets, the sequence number is one larger than the sequence number of the last byte in the last data segment of the connection.
  - For RESET segments, the sequence number is set to zero.
- Maximum Segment Size (MSS): 1000 bytes (excluding STP header)
- Data Field: Empty for all segments other than DATA
- STP Segments: Encapsulated within UDP segments.

## State Transitions

![State Diagram](https://i.imgur.com/Na6fRjn.png)

Receiver States
- CLOSED: Initial state.
- LISTEN: Waits for a SYN packet.
- ESTABLISHED: Moves here after receiving SYN and sending ACK.
- TIME_WAIT: After receiving FIN, waits for 2 seconds before closing.

Sender States
- CLOSED: Initial state.
- SYN_SENT: Sends SYN, waits for ACK.
- ESTABLISHED: After receiving ACK, starts data transmission.
- CLOSING: After sending all data, sends FIN.
- FIN_WAIT: Waits for ACK of FIN, then closes.

Key Processes
- Connection Setup: Two-way handshake (SYN, ACK).
- Connection Closure: One-way (FIN, ACK).
- Error Handling: Sends RESET on unexpected behavior.

## Protocol Features

### Common Features

Sequence Number Computation:
- Follows the standard as outlined in the Segment Format section.

RESET Segment:
- Either endpoint sends a RESET segment if it encounters unexpected behavior.

Logging:
- Both endpoints maintain a log file to record information about each segment sent and received. Details are provided in the Logs section.

### Sender Features

Two-Way Handshake (SYN, ACK) for Connection Establishment:
- The sender sends a SYN segment, and the receiver responds with an ACK. If the ACK is not received within the retransmission timeout (RTO), the sender should retransmit the SYN. After three unsuccessful attempts, a RESET segment must be sent to the receiver, and the sender transitions to the CLOSED state.

Two-Way Handshake (FIN, ACK) for Connection Termination:
- After successfully transmitting the entire file, the sender initiates connection closure by sending a FIN segment. The receiver responds with an ACK. If the ACK is not received within the RTO, the sender should retransmit the FIN. After three unsuccessful attempts, a RESET segment must be sent to the receiver, and the sender transitions to the CLOSED state.

Initial Sequence Number (ISN):
- A random ISN is chosen in the range of 0 to 2^16 - 1.

Sliding Window Protocol:
- Multiple segments can be transmitted in a pipelined manner. The sender maintains a buffer for all unacknowledged segments, and the total data transmitted but awaiting acknowledgment is limited by max_win. As the sender receives ACK segments, the left edge of the window slides forward, allowing transmission of the next data segments.
- When `max_win` is set to 1000 bytes, STP effectively behaves as a stop-and-wait protocol, where the sender transmits one data segment at a time and waits for the corresponding ACK segment.

Single Timer:
- The sender maintains a single timer and retransmits the oldest unacknowledged segment if the timer expires. The sender does not retransmit all unacknowledged segments, as Go-Back-N is not within scope.

Duplicate ACKs:
- The sender assumes that data up to the current ACK sequence number has been received and does not retransmit data simply because the ACK of earlier data was lost or delayed.

Fast Retransmit:
- The sender retransmits a segment upon receiving three duplicate ACKs.

### Receiver Features

Receive Window:
- Receiver initializes a receive window to store all received data. It can be set to a large value (e.g., 16KB) to hold all the data the sender can transmit in a pipelined manner (max_win).

Immediate ACK Generation:
- Receiver generates an ACK immediately upon receiving any segment from the sender.

Out-of-Order Segment Buffering:
- Receiver buffers out-of-order segments to ensure reliable in-order delivery of data.

Duplicate Data Handling:
- Receiver discards duplicate data that has already been received but was retransmitted due to a lost ACK.

Data Writing:
- Receiver writes received data to a file.

Unreliable Communication Emulation:
- Receiver simulates an unreliable communication channel with random forward and reverse loss probabilities.

Connection Closure:
- Upon receiving the FIN, the receiver waits for 2 seconds before transitioning to the CLOSED state.

### Excluded Features

- Timeout estimation
- Doubling the timeout interval
- Delayed ACKs
- Flow control or congestion control
- Handing re-ordered or corrupted segments
- Go-Back-N retransmission

## Assumptions

- Receiver is executed first before the sender.
- Sender and receiver shall exit after file delivery.
- Command-line arguments are always provided in the expected format.
- Sender and receiver are executed on the same machine.
- Segments are not re-ordered or corrupted.
- Very large values for `max_win` will not be used.
- File to send does not contain any CRLF terminator.
- File to send is not larger than 800 KB.
- Multi-threading is preferred in the implementation.

## Sender Program

To run the program, use:

```sh
python3 sender.py sender_port receiver_port file_to_send max_win rto
```

### Command Arguments

- `sender_port`: Port used by the sender for communication (value between 49152 and 65535).
- `receiver_port`: Port used by the receiver for communication (value between 49152 and 65535).
- `file_to_send`: Path of the text file to be transferred to the receiver.
- `max_win`: Maximum number of data bytes that the sender can transmit in a pipelined manner (does not include headers and must be a multiple of MSS, i.e., a multiple of 1000 bytes).
- `rto`: Retransmission timer value in milliseconds (unsigned integer).

### Design Justification

- Sender program has two threads: one for incoming traffic and one for outgoing traffic.
- Listener thread monitors incoming packets and updates state variables without immediate replies.
- Main thread handles heavy outgoing tasks, including file I/O, buffer management, segment composition, and transmission control.
- Phase handlers in the main thread respond to state changes signalled by the listener.
- Phase handlers initiate temporary executors on new threads for timed actions, such as retransmissions, using a TTL timer for expiration.
- Buffers are primarily arrays that store payloads and status flags.
- This approach reduces processing time for both traffic directions, enhancing responsiveness, especially during pipelined transmission.

## Receiver Program

To run the program, use:

```sh
python3 receiver.py receiver_port sender_port file_received flp rlp
```

### Command Arguments

- `receiver_port`: Port used by the receiver for communication (value between 49152 and 65535).
- `sender_port`: Port used by the sender for communication (value between 49152 and 65535).
- `file_received`: Path of the file to be created by the program, into which received data will be written.
- `flp`: Forward loss probability, the probability that each segment (SYN, DATA, FIN) sent from the sender to the receiver is dropped (a float between 0 and 1).
- `rlp`: Reverse loss probability, the probability that each segment (ACK) sent from the receiver to the sender is dropped (a float between 0 and 1).

### Design Justification

- Receiver is designed similarly to the sender, but the listener also sends packets in its thread.
- Receiver does not send data packets in a pipelined manner as it is configured to immediately react to incoming packets.
- Main thread primarily handles file I/O and connection termination.
- Buffers are implemented as dictionaries to accommodate the unknown size of data transfers.

## Trace Logs

### output/sender_log.txt

```
<snd/rcv> <time> <type of packet> <seq-number> <number-of-bytes>
Data Transferred: ?? bytes
Data Segments Sent: ??
Retransmitted Data Segments: ??
Duplicate Acknowledgements: ??
```

### output/receiver_log.txt

```
<snd/rcv/drp> <time> <type of packet> <seq-number> <number-of-bytes>
Data Received: ?? bytes
Data Segments Received: ??
Duplicate Data Segments Received: ??
Data Segments Dropped: ??
ACK Segments Dropped: ??
```

### General Remarks

- `<packet type>` could be SYN, ACK, FIN, DATA, or RESET.
- `<snd/rcv/drp>` indicates whether the segment was sent, received, or dropped.
- Time should be in milliseconds and relative to when the SYN segment was sent/received.
- The SYN segment is always sent/received at time 0.
- The number of bytes should be zero for all segments other than DATA segments.
- Cumulative statistics should be recorded at the end of the file transfer.
- Full debug messages are printed to the terminal, providing a glimpse of how each segment flows in action.

## Other Findings

![Outgoing Processing Delay](https://i.imgur.com/VHloeMm.png)

- Under a sliding window setting, data packets are sent in batches, and corresponding ACKs are expected in batches as well.
- Even if the program attempts to react immediately to an ACK, other ACKs may have arrived before new data packets are sent.
- Processing delays prevent packets from being sent between ACKs, as the time is too short for the program to react, process changes, and send new segments.
- The ideal sliding window log would show a crossover of ACK and DATA packets: `DATA DATA DATA ACK DATA ACK DATA ACK DATA ACK ACK ACK`.
- The program produces a log that appears separated in batches: `DATA DATA DATA ACK ACK ACK DATA DATA DATA ACK ACK ACK`.
- For example, ACK for data packet #1 was received at 28.27ms, but due to processing delays, data packet #4 was sent at 39.64ms, resulting in a delay of around 11ms.
- This delay caused subsequent ACKs to arrive, creating an illusion in the log that suggests the program stopped and waited after each batch.
- This is particularly significant when both the sender and receiver runs on the same machine via a localhost interface with minimal latency.
- Running sender and receiver on separate machines or networks may enable better observation of the standard sliding window behaviour in action.

## Test Cases

Test 1 - Stop and Wait over a Reliable Channel
```
python3 receiver.py 56007 59606 output/unicode.txt 0 0
python3 sender.py 59606 56007 data/unicode.txt 1000 100
```

Test 2 - Stop and Wait over an Unreliable Channel
```
python3 receiver.py 56007 59606 output/unicode.txt 0.1 0.1
python3 sender.py 59606 56007 data/unicode.txt 1000 100
```

Test 3 - Sliding Window over a Reliable Channel
```
python3 receiver.py 56007 59606 output/unicode.txt 0 0
python3 sender.py 59606 56007 data/unicode.txt 5000 100
```

Test 4 - Sliding Window over an Unreliable Channel
```
python3 receiver.py 56007 59606 output/unicode.txt 0.1 0.1
python3 sender.py 59606 56007 data/unicode.txt 5000 100
```
