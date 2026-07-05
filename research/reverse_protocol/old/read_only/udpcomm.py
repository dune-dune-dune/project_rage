#!/usr/bin/python
from __future__ import print_function
from time import time
import struct
from datetime import datetime
import codecs
from collections import deque
from traceback import print_exc
from twisted.internet.protocol import DatagramProtocol
from twisted.internet import reactor
from twisted.internet.task import LoopingCall
import traceback
from hashlib import sha256 as hash_alg
from pathlib import Path


class UdpProtocolSample(object):
	COMMAND_RESPONCES_PKTS = []
	def ComposeUdpPacket(self):
		return 0, "txpacketdata"
	def ParseUdpPacket(self, type, data, loop_time):
		return True

class UdpProtocolTwisted(DatagramProtocol):
	COMMAND_FORMAT = "!Bx H" # type, pad0, seq
	RESP_FORMAT = "!Bx H" # pkt_type, pad0, seq
	COMMAND_SIZE = struct.calcsize(COMMAND_FORMAT)
	RESP_SIZE = struct.calcsize(RESP_FORMAT)
	SALT_SIZE = 32
	HASH_SIZE = 4
	SEQ_RANGE = 0xFFFF

	def __init__(self, udp_host, udp_port,  period, timeout,
				 listen_port = None, on_data = None, on_error = None,
				 on_restored = None, on_tx = None, hash_salt = None):
# 		print "proto init", period, timeout
		self.udp_host     = udp_host 
		self.udp_tx_port  = udp_port 
		self.udp_rx_port  = listen_port if listen_port else udp_port + 1 
		self.period       = period
		self.timeout      = timeout
		self.rx_timeout_call = None
		self.on_data      = on_data
		self.on_error     = on_error
		self.on_restored  = on_restored
		self.on_tx        = on_tx
		self.connected    = False
		self.listener     = None 
		self.retry        = None
		self.tx_call    = None
		self.seq = 0
		self.tx_history_size = int(self.timeout / self.period) if self.timeout and self.period else 1000
		self.tx_times = {}
		if hash_salt is None or hash_salt == 'None':
			self.hash_salt = None
			self.HASH_SIZE = 0
		elif isinstance(hash_salt, (bytes, bytearray)):
			self.hash_salt = hash_salt
		elif isinstance(hash_salt, (str, Path)):
			with open(hash_salt, 'rb') as f:
				self.hash_salt = f.read(self.SALT_SIZE)
		elif hasattr(hash_salt, 'read'):
			self.hash_salt = hash_salt.read(self.SALT_SIZE)
		else:
			self.hash_salt = hash_salt

				 
	def __del__(self):
		self.Stop()
	def _install_listener(self):
		try:
			self.listener = reactor.listenUDP(self.udp_rx_port, self)
			# Catch Responces to  datagramReceived
# 			print("listener installed", self.udp_rx_port)
			return True
		except Exception as exc:
			self._process_error("listener faulted", exc)
			return False
	def _uninstall_listener(self):
		try:
			if self.listener:
				self.listener.stopListening()
# 				print("listener uninstalled")
		except Exception as exc:
# 			print("listener faulted", exc)
			pass 
		self.listener = None
	def _do_tx(self):
		try:
			if not self.listener:
				self._install_listener()
			if self.transport:
				self.seq = (self.seq + 1) & self.SEQ_RANGE
				pkt_type, pkt_data = self.ComposeUdpPacket()
				responce_type = self.COMMAND_RESPONCES_PKTS.get(pkt_type, None)
				if responce_type:
					tx_data = (self.seq, time())
					tx_times = self.tx_times.get(responce_type, None)
					if tx_times is None:
						self.tx_times[responce_type] = deque([tx_data], maxlen = self.tx_history_size)
					else:
						tx_times.append(tx_data)
				datagram = struct.pack(self.COMMAND_FORMAT, pkt_type, self.seq) + pkt_data
				if self.HASH_SIZE:
					hash = hash_alg(datagram)
					hash.update(self.hash_salt)
					datagram += hash.digest()[:self.HASH_SIZE]
#				print("\nTX: %d!"%self.seq, self.udp_host, self.udp_tx_port, self.tx_times, datagram.hex(' '))
				self.transport.write(datagram, (self.udp_host, self.udp_tx_port))
				if self.on_tx: self.on_tx(self.seq)
#			else:
# 				print("\nTX: no transport!")
		except Exception as exc:
			print("\nTX exc:", exc)
			self._process_error("tx faulted", exc)
#			traceback.print_exc()
	def _start_tx_loop(self):
# 		print("\nTX starts: ", self.period)
		self.tx_call = LoopingCall(self._do_tx)
		self.tx_call.start(self.period)
	def _stop_tx_loop(self):
		if self.tx_call is not None:
			self.tx_call.stop()
			self.tx_call = None
	def _restart_rx_timeout(self):
		self._cancel_rx_timeout()
		if self.timeout:
			self.rx_timeout_call = reactor.callLater(self.timeout, self._process_rx_timeout)
	def _process_rx_timeout(self):
		self.rx_timeout_call = None
		if self.OnConnectionTimeout: self.OnConnectionTimeout()
	def _cancel_rx_timeout(self):
		if self.rx_timeout_call is not None:
			self.rx_timeout_call.cancel()
			self.rx_timeout_call = None
	def _process_error(self, *args):
		if self.connected:
			self.connected = False
			if self.on_error: self.on_error()
			self.OnConnectionError(*args)
	def startProtocol(self):
#		self.transport.connect(self.udp_host, self.udp_tx_port)
# 		print("proto started", self.period)
		pass
	def datagramReceived(self, datagram, src):
		try:
			if len(datagram) < self.HASH_SIZE + self.RESP_SIZE:
				print("datagram too short")
				return False
			if self.HASH_SIZE:
				hash = hash_alg(datagram[:-self.HASH_SIZE])
				hash.update(self.hash_salt)
				if datagram[-self.HASH_SIZE:] == hash.digest()[:self.HASH_SIZE]:
					datagram = datagram[:-self.HASH_SIZE]
				else:
					print("hash mismatch!!")
					return False
			(rx_type, rx_seq) = struct.unpack(self.RESP_FORMAT, datagram[:self.RESP_SIZE])
# 			print("RX: len=%d, type=%d, seq=%d: "%(len(datagram), rx_type, rx_seq), self.tx_times)
			tx_times = self.tx_times.get(rx_type, None)
			if tx_times:
				while tx_times:
					tx_seq, tx_time = tx_times[0]
					seq_latency = (rx_seq - tx_seq)
					if seq_latency > self.SEQ_RANGE / 2: seq_latency -= self.SEQ_RANGE + 1 
					elif seq_latency <= -self.SEQ_RANGE / 2: seq_latency += self.SEQ_RANGE - 1 
					if tx_seq == rx_seq: # seq_latency == 0
# 						print("pkt %d seq %d == %d"%(rx_type, tx_seq, rx_seq))
						now_time = time()
						loop_time = now_time - tx_time
						if self.ParseUdpPacket(rx_type, datagram[self.RESP_SIZE:], loop_time):
							self._restart_rx_timeout()
							if not self.connected:
								self.connected = True
								if self.on_restored: self.on_restored()
							if self.on_data: self.on_data()
# 						else:
# 							print("pkt type %d not parsed "%rx_type, loop_time)
					elif seq_latency > self.SEQ_RANGE / 16 or seq_latency <= -self.SEQ_RANGE / 16:
# 						print("pkt %d rx_seq out of allowed, rx junk ignored, rx:%d tx:%d, d:%d) "%(rx_type, rx_seq, tx_seq, seq_latency))
						break
					elif seq_latency < 0:
# 						print("pkt %d rx:%d responce too late, min expected tx:%d is %d ahead, rx ignore"%(rx_type, rx_seq, tx_seq, seq_latency))
						break
					elif seq_latency > 0:
# 						print("pkt %d tx:%d responce missed, new rx:%d got %d ahead, tx drop"%(rx_type, tx_seq, rx_seq, seq_latency))
						pass
					tx_times.popleft()
			else:
				self.ParseUdpPacket(rx_type, datagram[self.RESP_SIZE:], 0)
		except Exception as err:
			print("parse error", rx_type, err)
#			traceback.print_exc()
		return False
	def connectionRefused(self):
		print("\nconnection refused")
		self._uninstall_listener()
		self._process_error("connection refused")
	def Start(self): 
# 		print("protocol start")
		self._start_tx_loop()
	def Stop(self): 
# 		print("protocol stop")
		self._stop_tx_loop()
		self._cancel_rx_timeout()
		self._uninstall_listener()

class CommunicationAnimator():
	animation_seq_ok = ["[=---]", "[>---]", "[->--]", "[-->-]", "[--->]", "[---=]", "[---<]", "[--<-]", "[-<--]", "[<---]"]
	animation_seq_err = ["[ X  ]", "[-X  ]", "[=X  ]", "[#X  ]"]
	def __init__(self, default_speed = 1):
		self._animation_speed = default_speed
		self._n_ok = 0
		self._n_err = 0
	def AnimateOk(self, animation_speed = None):
# 		print("ok seq:", self._n_ok, " (", len(self.animation_seq_ok), ")", animation_speed, "<")
		if animation_speed is None: animation_speed = self._animation_speed
		seq = self.animation_seq_ok
		self._n_ok = (self._n_ok + animation_speed)%len(seq)
		return seq[int(self._n_ok)]
	def AnimateErr(self, animation_speed = None):
		if animation_speed is None: animation_speed = self._animation_speed
		seq = self.animation_seq_err
		self._n_err = (self._n_err + animation_speed)%len(seq)
		return seq[int(self._n_err)]

		
class TestEchoProtocol(DatagramProtocol):
	def __init__(self, reply_port=None):
		self.reply_port = reply_port
	def datagramReceived(self, data, src):
		(host, port) = src
		print(datetime.now().time(),
			"received %u:%32s from %s:%d" % (len(data), codecs.encode(data[:16],'hex_codec'), host, port))
		self.transport.write(data, (host, (self.reply_port if self.reply_port else port)))

class TestServer():
	def __init__(self, port, protocol):
		self.port = port
		self.protocol = protocol
	def run(self):
		reactor.listenUDP(self.port, self.protocol)
		print(datetime.now().time(), "Listening at port:", self.port)
		reactor.run()

def parse_args():
	from argparse import ArgumentParser
	parser = ArgumentParser(description='UDP communication test.')
	parser.add_argument("-s", "--server", help="test server mode (echo)",
					type = bool, default = True)
	parser.add_argument("-a", "--host", "--address", help="target host ip name or address",
					default = "127.0.0.1")
	parser.add_argument("-p", "--port", help="listed ip port (UDP)",
					type = int, required = True)
	parser.add_argument("-r", "--reply-port", help="reply ip port (UDP)",
					type = int)
	args = parser.parse_args()
	return args 

if __name__ == '__main__':
	args = parse_args()
	if args.server:
		TestServer(args.port, TestEchoProtocol(args.reply_port)).run()

