from __future__ import print_function
from time import sleep, time
from sys import stdout
import locale

class StatusLinePrinter(object):
	def __init__(self, format):
		self._fmt = format
		self._width, self._height = getTerminalSize()
		self.clean()
	def __del__(self):
		self.clean()
	def _print(self):
		stdout.write("{:<{w}.{w}s}\r".format(self._text, w=self._width-1))
		stdout.flush()
	def clean(self):
		self._text = ''
		self._print()
	def print(self, *args, **kwargs):
		self._text = self._fmt.format(self, *args, **kwargs)
		self._print()

class CommandLineKeyReader(object):
	POSIX_KEYS = {
		0x1B: { # ESC
			10:'AltEnter', 127:'AltBackspace', 8:'AltCtrlBackspace', 9:'AltTab',
			0x1B: { # ESC ESC
				0x5B: { # ESC ESC [
					0x31: { # ESC ESC [ 1
						0x7E:'AltHome',
						0x31:{ 0x7E:'AltF1' },
						0x32:{ 0x7E:'AltF2' },
						0x33:{ 0x7E:'AltF3' },
						0x34:{ 0x7E:'AltF4' },
					},
					0x32:{ 0x7E:'AltIns' },
					0x33:{ 0x7E:'AltDel' },
					0x34:{ 0x7E:'AltEnd' },
					0x35:{ 0x7E:'AltPgUp' },
					0x36:{ 0x7E:'AltPgDn' },
					0x41:'AltUp', 0x42:'AltDown', 0x43:'AltRight', 0x44:'AltLeft',
				},
			},
			0x4F: { # ESC O
				0x41:'CtrlUp', 0x42:'CtrlDown', 0x43:'CtrlRight', 0x44:'CtrlLeft',
				0x50:'F1', 0x51:'F2', 0x52:'F3', 0x53:'F4',
			},
			0x5B: { # ESC [
				0x31: { # ESC [ 1
					0x7E:'Home',
					0x31:{ 0x7E:'F1' },
					0x32:{ 0x7E:'F2' },
					0x33:{ 0x7E:'F3' },
					0x34:{ 0x7E:'F4' },
					0x3B: { # ESC [ 1 ;
						0x32:{ # ESC [ 1 ; 2
							0x41:'ShiftUp', 0x42:'ShiftDown', 0x43:'ShiftRight', 0x44:'ShiftLeft',
							0x50:'ShiftF1', 0x51:'ShiftF2', 0x52:'ShiftF3', 0x53:'ShiftF4',
						},
						0x33:{ # ESC [ 1 ; 3
							0x41:'AltUp', 0x42:'AltDown', 0x43:'AltRight', 0x44:'AltLeft',
							0x50:'AltF1', 0x51:'AltF2', 0x52:'AltF3', 0x53:'AltF4',
						},
						0x35:{ # ESC [ 1 ; 5
							0x41:'CtrlUp', 0x42:'CtrlDown', 0x43:'CtrlRight', 0x44:'CtrlLeft',
							0x50:'CtrlF1', 0x51:'CtrlF2', 0x52:'CtrlF3', 0x53:'CtrlF4',
						},
					},
				},
				0x32:{ # ESC [ 2
					0x7E:'Ins',
					0x33:{0x7E:'ShiftF1'}, 0x34:{0x7E:'ShiftF2'}, 0x35:{0x7E:'ShiftF3'}, 0x36:{0x7E:'ShiftF4'},
				},
				0x33:{ 0x7E:'Del' },
				0x34:{ 0x7E:'End' },
				0x35:{ 0x7E:'PgUp' },
				0x36:{ 0x7E:'PgDn' },
				0x41:'Up', 0x42:'Down', 0x43:'Right', 0x44:'Left',
				0x46:'End', 0x48:'Home',
			},
		},
		10:'Enter', 127:'Backspace', 8:'CtrlBackspace', 9:'Tab',
	}
	WINDOWS_KEYS = {
		0x00: {
			0x3B:'F1', 0x54:'ShiftF1', 0x5E:'CtrlF1', 0x68:'AltF1',
			0x3C:'F2', 0x55:'ShiftF2', 0x5F:'CtrlF2', 0x69:'AltF2',
			0x3D:'F3', 0x56:'ShiftF3', 0x60:'CtrlF3', 0x6A:'AltF3',
			0x3E:'F4', 0x57:'ShiftF4', 0x61:'CtrlF4', 0x6B:'AltF4',
			0x98:'AltUp', 0xA0:'AltDown', 0x9D:'AltRight', 0x9B:'AltLeft',
			0x99:'AltPgUp', 0xA1:'AltPgDn', 0x97:'AltHome', 0x9F:'AltEnd', 0xA2:'AltIns', 0xA3:'AltDel',
		},
		0xE0: {
			0x48:'Up', 0x50:'Down', 0x4D:'Right', 0x4B:'Left',
			0x8d:'CtrlUp', 0x91:'CtrlDown', 0x74:'CtrlRight', 0x73:'CtrlLeft',
			0x49:'PgUp', 0x51:'PgDn', 0x47:'Home', 0x4F:'End', 0x52:'Ins', 0x53:'Del',
			0x86:'CtrlPgUp', 0x76:'CtrlPgDn', 0x77:'CtrlHome', 0x75:'CtrlEnd', 0x92:'CtrlIns', 0x93:'CtrlDel',
		},
		27:'Esc', 13:'Enter', 10:'CtrlEnter', 8:'Backspace', 127:'CtrlBackspace', 9:'Tab',
	}
	def __init__(self):
		try:
			# POSIX system. Create and return a getch that manipulates the tty.
			import termios, sys, tty, select, atexit
			self.fd = sys.stdin.fileno()
			self.old_settings = termios.tcgetattr(self.fd)
			atexit.register(self._restore)
			new_settings = termios.tcgetattr(self.fd)
			new_settings[3] &= ~termios.ICANON & ~termios.ECHO
			termios.tcsetattr(self.fd, termios.TCSAFLUSH, new_settings)
			def _getch(wait = False):
				if not wait and select.select([sys.stdin], [], [], 0)[0] == []:
					return '', None
				ch = sys.stdin.read(1)
				return (ch, ord(ch))
			self.getch = _getch
			self.keymap = CommandLineKeyReader.POSIX_KEYS
		except ImportError:
			# Non-POSIX. Return msvcrt's (Windows') getch.
			import msvcrt
			def _getch(wait = False):
				if not wait and not msvcrt.kbhit():
					return ('', None)
				ch = msvcrt.getch()
				return (ch.decode(locale.getpreferredencoding(), 'ignore'), ord(ch))
			self.getch = _getch
			self.keymap = CommandLineKeyReader.WINDOWS_KEYS
	def _restore(self):
		import termios
		termios.tcsetattr(self.fd, termios.TCSAFLUSH, self.old_settings)
	def __call__(self):
		readable = ''
		ch, code = self.getch()
# 		if ch: print('0x%02x, %s'%(code, repr(ch)))
		keymap = self.keymap
		while code in keymap:
			value = keymap[code]
			if isinstance(value, dict):
				keymap = value
				readable += '%02x.'%code
				ch, code = self.getch(True)
# 				if ch: print('0x%02x, %s'%(code, repr(ch)))
# 				else: print(repr((ch, code)))
			else:
				return value
		if readable:
			return readable + '%02x'%code
		return ch

def getTerminalSize(default_value=(80,25)):
	import platform
	current_os = platform.system()
	tuple_xy = None
	if current_os == 'Windows':
		tuple_xy = _getTerminalSize_windows()
		if tuple_xy is None:
			tuple_xy = _getTerminalSize_tput()
			# needed for window's python in cygwin's xterm!
	if current_os == 'Linux' or current_os == 'Darwin' or  current_os.startswith('CYGWIN'):
		tuple_xy = _getTerminalSize_linux()
	if tuple_xy is None:
		tuple_xy = default_value
	return tuple_xy

def _getTerminalSize_windows():
	res = None
	try:
		from ctypes import windll, create_string_buffer

		# stdin handle is -10
		# stdout handle is -11
		# stderr handle is -12

		h = windll.kernel32.GetStdHandle(-12)
		csbi = create_string_buffer(22)
		res = windll.kernel32.GetConsoleScreenBufferInfo(h, csbi)
	except:
		return None
	if res:
		import struct
		(bufx, bufy, curx, cury, wattr,
		 left, top, right, bottom,
		 maxx, maxy) = struct.unpack("hhhhHhhhhhh", csbi.raw)
		sizex = right - left + 1
		sizey = bottom - top + 1
		return sizex, sizey
	else:
		return None

def _getTerminalSize_tput():
	# get terminal width
	# src: http://stackoverflow.com/questions/263890/how-do-i-find-the-width-height-of-a-terminal-window
	try:
		import subprocess
		proc=subprocess.Popen(["tput", "cols"],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
		output=proc.communicate(input=None)
		cols=int(output[0])
		proc=subprocess.Popen(["tput", "lines"],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
		output=proc.communicate(input=None)
		rows=int(output[0])
		return (cols,rows)
	except:
		return None

def _getTerminalSize_linux():
	def ioctl_GWINSZ(fd):
		try:
			import fcntl, termios, struct, os
			cr = struct.unpack('hh', fcntl.ioctl(fd, termios.TIOCGWINSZ,'1234'))
		except:
			return None
		return cr
	cr = ioctl_GWINSZ(0) or ioctl_GWINSZ(1) or ioctl_GWINSZ(2)
	if not cr:
		try:
			fd = os.open(os.ctermid(), os.O_RDONLY)
			cr = ioctl_GWINSZ(fd)
			os.close(fd)
		except:
			pass
	if not cr:
		try:
			cr = (env['LINES'], env['COLUMNS'])
		except:
			return None
	return int(cr[1]), int(cr[0])

def test():
	sizex,sizey = getTerminalSize()
	print('terminal width =',sizex,'height =',sizey)

	n = 0
	kr = CommandLineKeyReader()
	stp = StatusLinePrinter('key: {0.key}')
	print("press keys to show keycodes, 'q' to quit")
	stp.key = ""
	while(True):
		ch = kr()
		if ch:
			n = 20
			stp.key += ' ' + repr(ch)
		elif n == 1:
			stp.key = ""
			n = 0
		elif n:
			n -= 1;
		stp.print()
		if ch.lower() in ('q', 'esc'):
			exit(0)
		sleep(0.1)

if __name__ == '__main__':
	test()
