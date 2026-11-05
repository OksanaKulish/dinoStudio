import { spawn } from 'node:child_process';
import { createServer } from 'node:net';

async function findPort(start) {
	for (let port = start; port <= 4319; port++) {
		const available = await new Promise(resolve => {
			const probe = createServer();
			probe.once('error', () => resolve(false));
			probe.listen(port, '127.0.0.1', () => probe.close(() => resolve(true)));
		});
		if (available) return port;
	}
	throw new Error('No free development port between 4300 and 4319.');
}

const appPort = await findPort(4300);
const server = spawn(process.execPath, ['scripts/terminal-server.mjs'], {
	stdio: 'inherit', env: { ...process.env, DINO_APP_PORT: String(appPort), DINO_API_PORT: String(appPort + 1) },
});
const angular = spawn(process.execPath, ['node_modules/@angular/cli/bin/ng.js', 'serve', '--port', String(appPort)], { stdio: 'inherit' });
console.log(`DinoStudio: http://localhost:${appPort}/`);

function stop() {
	server.kill();
	angular.kill();
}

process.on('SIGINT', stop);
process.on('SIGTERM', stop);
server.on('exit', code => {
	angular.kill();
	process.exitCode = code || 1;
});
angular.on('exit', code => {
	server.kill();
	process.exitCode = code || 0;
});