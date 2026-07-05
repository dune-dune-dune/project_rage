import logging
from threading import Thread
import asyncio
import re

from utils import get_listening_ip


class AsyncUDPProtocol(asyncio.DatagramProtocol):
    SERVER_HOST = '192.168.88.128'
    # SERVER_HOST = '192.168.88.33'
    SERVER_PORT = 20444
    CLIENT_HOST = None
    CLIENT_PORT = None
    logger = None

    def __init__(self):
        super().__init__()
        self.data = b''
        self.closed = None

    def connection_made(self, transport):
        self.transport = transport
        self.closed = asyncio.get_running_loop().create_future()

    def connection_lost(self, exc):
        if self.closed and not self.closed.done():
            self.closed.set_result(exc)

    def datagram_received(self, data, addr):
        self.data = data
        if self.logger:
            self.logger.info(f"External rangefinder: Received message. {data} from client {addr}")

    def send_on(self, count):
        self.transport.sendto(f'on_{count}'.encode(), (self.CLIENT_HOST, self.CLIENT_PORT))

    def send_off(self):
        self.transport.sendto(f'off_1'.encode(), (self.CLIENT_HOST, self.CLIENT_PORT))


class ExternalRangefinderCommunicator(object):
    def __init__(self, client_host, client_port, timout, logger):
        self.timeout = timout
        self.protocol_class = AsyncUDPProtocol
        self.protocol_class.CLIENT_PORT = client_port
        self.protocol_class.CLIENT_HOST = client_host
        self.running = False
        self.loop = None
        self.transport = None
        self.protocol = None
        self.expression = r'[0-9]+[.][0-9]'
        self.__distance = None
        self.protocol_class.logger = logger
        self.protocol_class.SERVER_HOST = get_listening_ip(client_host, client_port)

    @property
    def distance(self):
        data = self.protocol.data if self.protocol else None
        found_distance = re.findall(self.expression, data.decode()) if data else None
        if found_distance:
            self.__distance = float(found_distance[0])
        return self.__distance

    @distance.setter
    def distance(self, value):
        self.__distance = value

    def launch(self, callback):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._start(callback))
        finally:
            self.loop.close()
            self.loop = None
            asyncio.set_event_loop(None)

    def launch_in_thread(self, callback):
        Thread(target=self.launch, daemon=True, args=(callback,)).start()

    async def _start(self, callback):
        self.distance = None
        self.transport, self.protocol = await self.loop.create_datagram_endpoint(
            AsyncUDPProtocol, local_addr=(AsyncUDPProtocol.SERVER_HOST, AsyncUDPProtocol.SERVER_PORT))
        try:
            for _ in range(self.timeout * 10):
                self.protocol.send_on(1)
                if self.distance:
                    break
                await asyncio.sleep(.1)
        finally:
            self.protocol.send_off()
            self.transport.close()
            if self.protocol.closed is not None:
                await self.protocol.closed
            self.protocol = None
            self.transport = None
            if self.distance:
                callback()

    def send_on(self, count):
        self.protocol.send_on(count)

    def send_off(self):
        self.protocol.send_off()


if __name__ == '__main__':
    CLIENT_HOST = '192.168.88.97'
    CLIENT_PORT = 20424
    comm = ExternalRangefinderCommunicator(
        CLIENT_HOST, CLIENT_PORT, 7, logger=logging.getLogger(__name__))
    comm.launch(callback=lambda: print('# done'))
    print(f'[i] Distance: {comm.distance}')
    print('[i] Done')
