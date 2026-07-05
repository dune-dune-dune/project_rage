from functools import wraps
import logging
from threading import Thread
import datetime
import os
import re
import signal
from socket import socket, AF_INET, SOCK_DGRAM

class AntiBugThread(Thread):
    def run(self, *a, **kwa) -> None:
        # Logger.debug("thread: Start running thread id=%s, name=%s, self=%s, args=%s, kwargs=%s", id(self), self.name, str(self), str(a), str(kwa))
        super().run(*a, **kwa)
        # Logger.debug("thread: Finished running thread id=%s, name=%s, self=%s, args=%s, kwargs=%s", id(self), self.name, str(self), str(a), str(kwa))


def now(x=None):
    return x or datetime.datetime.now()


class CONSTANTS:
    @classmethod
    def items(cls):
        return sorted([getattr(cls, k) for k in dir(cls) if (k[:2] != '__') and isinstance(getattr(cls, k), str)])


class ABOS:

    @staticmethod
    def get_space(d, total_or_free='total'):
        statvfs = os.statvfs(d)
        return statvfs.f_frsize * (statvfs.f_bavail if 'free' == total_or_free else statvfs.f_blocks)

    @staticmethod
    def join(*args):
        return os.path.join(*[a for a in args if a])

    @staticmethod
    def add_tailing_separator(d):
        return d if d[-1] == os.sep else f"{d}{os.sep}"

    @staticmethod
    def remove_tailing_separator(d):
        return d[:-1] if (len(d) and d[-1]) == os.sep else d

    @staticmethod
    def getmtime(fname):
        return datetime.datetime.fromtimestamp(os.path.getmtime(fname))

    @staticmethod
    def get_file_size(fname, if_not_exists=None):
        try:
            return os.stat(fname).st_size
        except Exception as e:
            return if_not_exists

    @staticmethod
    def is_dir_writable(directory, actually_try_write_file='.antibugtest'):
        logging.debug("UTILS: d=%s, isdir=%s, wok=%s, xok=%s",directory, os.path.isdir(directory), os.access(directory, os.W_OK), os.access(directory, os.X_OK))
        if not os.path.isdir(directory) or not os.access(directory, os.W_OK) or not os.access(directory, os.X_OK):
            return False

        if actually_try_write_file:
            try:
                destdir = ABOS.join(directory, actually_try_write_file)
                logging.debug("UTILS: destdir=%s", destdir)
                with open(destdir, 'w') as filehandle:
                    filehandle.write(" ")
                os.remove(destdir)
                return True
            except IOError:
                return False
        else:
            return True


def ignore_exceptions(function=None, return_on_exception=None):
    """
    Use cases:
    @ignore_exceptions
    def some_func():
        pass

    @ignore_exceptions(return_on_exception=<value>)
    def some_func():
        pass
    :param return_on_exception: value returned if any exception occurs
    """
    def decorator_per_se(f):
        @wraps(f)
        def run(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except Exception as e:
                logging.error("Exception in function %s: %s", str(f), str(e))
                return return_on_exception

        return run

    return decorator_per_se(function) if function else decorator_per_se

def bool2float(x):
    x = 1. if x is True else x
    x = 0. if x is False else x
    return max(0, min(1, x))


def replaced(o, subs, prefix=''):
    regexp = re.compile(re.escape(prefix) + r'{(?P<key>[a-zA-Z-0-9_\-.]+)}')
    if str in type(o).__mro__:
        ret_r = ""
        prev = 0
        for f in regexp.finditer(o):
            ret_r += (o[prev:f.regs[0][0]] + str(subs.get(f['key'], f[0])))
            prev = f.regs[0][1]
        return ret_r + o[prev:]
    elif dict in type(o).__mro__:
        ret = type(o)()
        for k, v in o.items():
            ret[k] = replaced(v, subs, prefix=prefix)
        return ret
    else:
        logging.warning("UTILS: Expected first argument type='%s' is str or dict. Return it unchanged", o, type(o))
        return o


def run_in_thread(function=None, on_done=None, daemon=True, no_flood=False, mandatory_call_last_if_flood=True, name=None):
    """
    Use cases:
    @run_in_thread
    def some_func():
        pass

    @run_in_thread(on_done=lambda res: print(f'function `some_func` finished and returns value {res}'))
    def some_func():
        pass

    :param on_done: function called after decorated function returns
    :param mandatory_call_last_if_flood: execute last call also if flood (ignored if no_flood=False)
    :param no_flood: only one instance of function is called at a time, on_done is called after LAST `function` call,
    paralell on_done calls is not considered as flood, so, it should be `antiflooded` by it's own.
    If function is called many times first AND if mandatory_call_last_if_flood==True LAST call will succeed
    :param daemon: daemon parameters passed to threading.Thread (True/False/None)
    """
    def decorator_per_se(f):

        no_flood_semaphore = {'call_again': False, 'in_progress': False}

        def in_thread():
            ret = None
            while no_flood_semaphore['call_again']:
                c_a_args = no_flood_semaphore['call_again']['a']
                c_a_kwargs = no_flood_semaphore['call_again']['kwa']
                no_flood_semaphore['call_again'] = False
                try:
                    ret = f(*c_a_args, **c_a_kwargs)
                except Exception as exp:
                    logging.exception(exp)

            no_flood_semaphore['in_progress'] = False
            # Yes, in_progress should be changed BEFORE on_done call ()
            return ret if on_done is None else on_done(ret)

        @wraps(f)
        def run(*a, **kwa):
            if no_flood and no_flood_semaphore['in_progress']:
                if no_flood_semaphore['call_again']:
                    if mandatory_call_last_if_flood:
                        logging.warning('Discard prev function %s call with args=%s, kwargs=%a', f, str(no_flood_semaphore['call_again']['a']), str(no_flood_semaphore['call_again']['kwa']))
                        no_flood_semaphore['call_again'] = {'a': a, 'kwa': kwa}
                    else:
                        logging.warning('Discard function %s call with args=%s, kwargs=%a', f, str(a), str(kwa))
            else:
                no_flood_semaphore['in_progress'] = True
                no_flood_semaphore['call_again'] = {'a': a, 'kwa': kwa}
                t = AntiBugThread(target=in_thread, daemon=daemon, name=str(f) if name is None else name)
                t.start()
                return t

        return run

    return decorator_per_se(function) if function else decorator_per_se


def on_kill(f):

    @wraps(f)
    def run(self):

        def _on_kill(signal_num, stack_frame):
            f(self)

        signal.signal(signal.SIGINT, _on_kill)
        signal.signal(signal.SIGTERM, _on_kill)

    return run


def get_listening_ip(host, port):
    s = socket(AF_INET, SOCK_DGRAM)
    s.connect((host, port))
    res = s.getsockname()[0]
    s.close()
    return res


if __name__ == '__main__':
    import time

    def test_run_in_thread():

        counters = {'task1': 0, 'task2': 0}
        def l(m):
            logging.info(f'%.4f, %s', (now()-start).total_seconds(), m)

        @run_in_thread(on_done=lambda ret: l(f'{ret} on done'), no_flood=False)
        def task1():
            counters['task1'] += 1
            id = f"task 1.{counters['task1']}"
            l(f"{id} started")
            time.sleep(1)
            l(f"{id} finished")
            return id

        @run_in_thread(on_done=lambda ret: l(f'{ret} on done'), no_flood=True)
        def task2():
            counters['task2'] += 1
            id = f"task 2.{counters['task2']}"
            l(f"{id} started")
            time.sleep(1)
            l(f"{id} finished")
            return id

        start = now()
        for i in range(8):
            task1()
            time.sleep(0.1)

        time.sleep(2)

        start = now()
        for i in range(8):
            task2()
            time.sleep(0.1)

        time.sleep(3)


    def test_ignore_exception():

        print('TEST ignore_exceptions')

        @ignore_exceptions
        def func1(raise_exception):
            if raise_exception:
                raise Exception("Exception in func 1")
            return 'ret func 1'

        @ignore_exceptions(return_on_exception='exception result func 2')
        def func2(raise_exception):
            if raise_exception:
                raise Exception("Exception in func 2")
            return 'ret func 2'

        print("func1(False)=", func1(False))
        print("func1(True)=", func1(True))
        print("func2(False)=", func2(False))
        print("func2(True)=", func2(True))


    def test_on_kill():
        @on_kill
        def do_something_in_your_miserable_life():
            i = 0
            while i < 5:
                i += 1
                print(f'on_kill {i} of 5')
                time.sleep(1)

        def start():
            pid = os.getpid()
            print(f'my pid is {pid}')
            i = 0
            while i < 10:
                i += 1
                print(f'main cycle {i} of 10')
                time.sleep(1)
                if i == 3:
                    print(f'sending SIGTERM to myself')
                    os.kill(pid, signal.SIGTERM)

        start()

    # test_ignore_exception()
    # test_kivy_debounce()
    # test_on_kill()
    test_run_in_thread()

def change_log_format():

    from kivy.logger import KivyFormatter, ConsoleHandler, is_color_terminal

    # here we change default logging level (it prevent intrusive 'DEBUG' level logs from some imported modules (yes `onvif2-zeep` I mean you))
    logger_root = logging.getLogger('root')
    logger_root.setLevel(logging.INFO)

    # Here we add some time to logging
    logging.Formatter.default_msec_format = '%s.%03d'
    logging.Formatter.default_time_format = '%m-%d %H:%M:%S'
    for handler in logger_root.handlers:
        if isinstance(handler, ConsoleHandler):
            if is_color_terminal():
                # Add color to console if supported
                handler.setFormatter(KivyFormatter('[%(levelname)-18s] %(asctime)s %(message)s ║ %(filename)s:%(lineno)d'))
            else:
                handler.setFormatter(logging.Formatter('[%(levelname)-7s] %(asctime)s %(message)s ║ %(filename)s:%(lineno)d'))
        else:
            # [severity] is added already, why it is ignored
            handler.setFormatter(logging.Formatter('%(asctime)s ║ %(message)s'))
