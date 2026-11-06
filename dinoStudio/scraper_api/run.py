import asyncio
import os
import socket

import uvicorn


def open_available_socket(host: str, first_port: int, last_port: int) -> tuple[socket.socket, int]:
	for port in range(first_port, last_port + 1):
		sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			if os.name == "nt":
				sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
			else:
				sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
			sock.bind((host, port))
			sock.listen(2048)
			sock.setblocking(False)
			return sock, port
		except OSError:
			sock.close()
	raise RuntimeError(f"No free scraper API port in range {first_port}-{last_port}.")


async def serve() -> None:
	host = "127.0.0.1"
	first_port = int(os.environ.get("SCRAPER_API_PORT", "8000"))
	last_port = int(os.environ.get("SCRAPER_API_PORT_MAX", str(first_port + 20)))
	sock, port = open_available_socket(host, first_port, last_port)
	print(f"Scraper API: http://{host}:{port}/ (Angular CORS origin: {os.environ.get('DINO_APP_PORT', '4300')})", flush=True)
	config = uvicorn.Config("scraper_api.app.api.main:app", host=host, port=port, log_level="info")
	server = uvicorn.Server(config)
	try:
		await server.serve(sockets=[sock])
	finally:
		sock.close()


if __name__ == "__main__":
	asyncio.run(serve())