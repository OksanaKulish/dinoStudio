import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { stat } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, resolve, win32 } from 'node:path';
import { fileURLToPath } from 'node:url';

let port = Number(process.env.DINO_API_PORT || 4301);
const appPort = Number(process.env.DINO_APP_PORT || 4300);
const origins = new Set([`http://localhost:${appPort}`, `http://127.0.0.1:${appPort}`]);
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sessions = new Map();
let generatingImage = false;
let imageProgress = null;
let generatingCode = false;
let codeProgressPhase = null;
let codeProgress = null;

function runImage(prompt, deviceOnly = false) {
	return new Promise((resolveResult, reject) => {
		const venv = resolve(root, '.venv', 'Scripts', 'python.exe');
		const python = process.env.NOTEBOOK_PYTHON || (existsSync(venv) ? venv : 'py');
		const child = spawn(python, [resolve(root, 'scripts', 'generate-image.py'), ...(deviceOnly ? ['--device'] : [])], {
			cwd: root, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
		});
		let stdout = '';
		let stderr = '';
		let progressBuffer = '';
		let timedOut = false;
		const timer = setTimeout(() => { timedOut = true; child.kill(); }, deviceOnly ? 10_000 : 300_000);
		child.stdout.on('data', chunk => {
			stdout += chunk;
			if (stdout.length > 4_000_000) { timedOut = true; child.kill(); }
		});
		child.stderr.on('data', chunk => {
			const text = chunk.toString();
			stderr = (stderr + text).slice(-20_000);
			progressBuffer += text;
			while (progressBuffer.includes('\n')) {
				const newline = progressBuffer.indexOf('\n');
				const line = progressBuffer.slice(0, newline).trim();
				progressBuffer = progressBuffer.slice(newline + 1);
				const match = /^DINO_PROGRESS:(\d{1,3})$/.exec(line);
				if (match) imageProgress = Math.min(100, Number(match[1]));
			}
			if (progressBuffer.length > 10_000) progressBuffer = progressBuffer.slice(-1000);
		});
		child.on('error', error => { clearTimeout(timer); reject(error); });
		child.on('close', code => {
			clearTimeout(timer);
			if (timedOut) { reject(new Error('Image generation exceeded the time or output limit.')); return; }
			try {
				const result = JSON.parse(stdout);
				if (code !== 0 || result.error) reject(new Error(result.error || stderr || 'Image generation failed.'));
				else resolveResult(result);
			} catch (error) {
				reject(error instanceof SyntaxError ? new Error(stderr || 'Image generator returned no result.') : error);
			}
		});
		child.stdin.end(deviceOnly ? '' : JSON.stringify({ prompt }));
	});
}

function readDataFile(name, data) {
	return new Promise((resolveResult, reject) => {
		const venv = resolve(root, '.venv', 'Scripts', 'python.exe');
		const python = process.env.NOTEBOOK_PYTHON || (existsSync(venv) ? venv : 'py');
		const child = spawn(python, [resolve(root, 'scripts', 'read-dataframe.py')], {
			cwd: root, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
		});
		let stdout = '';
		let stderr = '';
		let stopped = false;
		const timer = setTimeout(() => { stopped = true; child.kill(); }, 30_000);
		child.stdout.on('data', chunk => {
			stdout += chunk;
			if (stdout.length > 32_000_000) { stopped = true; child.kill(); }
		});
		child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-10_000); });
		child.on('error', error => { clearTimeout(timer); reject(error); });
		child.on('close', code => {
			clearTimeout(timer);
			if (stopped) { reject(new Error('File processing exceeded the time or output limit.')); return; }
			try {
				const result = JSON.parse(stdout);
				if (code !== 0 || result.error) reject(new Error(result.error || stderr || 'Unable to read data file.'));
				else resolveResult(result);
			} catch (error) {
				reject(error instanceof SyntaxError ? new Error(stderr || 'Pandas returned no result.') : error);
			}
		});
		child.stdin.end(JSON.stringify({ name, data }));
	});
}

function generateCode(model, language, prompt) {
	return new Promise((resolveResult, reject) => {
		const venv = resolve(root, '.venv', 'Scripts', 'python.exe');
		const python = process.env.NOTEBOOK_PYTHON || (existsSync(venv) ? venv : 'py');
		const child = spawn(python, [resolve(root, 'scripts', 'generate-code.py')], {
			cwd: root, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
		});
		let stdout = '';
		let stderr = '';
		let progressBuffer = '';
		let timedOut = false;
		const timer = setTimeout(() => { timedOut = true; child.kill(); }, 600_000);
		child.stdout.on('data', chunk => {
			stdout += chunk;
			if (stdout.length > 1_000_000) { timedOut = true; child.kill(); }
		});
		child.stderr.on('data', chunk => {
			const text = chunk.toString();
			stderr = (stderr + text).slice(-20_000);
			progressBuffer += text;
			while (progressBuffer.includes('\n')) {
				const newline = progressBuffer.indexOf('\n');
				const line = progressBuffer.slice(0, newline).trim();
				progressBuffer = progressBuffer.slice(newline + 1);
				const match = /^DINO_CODE_PROGRESS:(loading|generating):(\d{1,3})$/.exec(line);
				if (match) {
					codeProgressPhase = match[1];
					codeProgress = Number(match[2]);
				}
			}
			if (progressBuffer.length > 10_000) progressBuffer = progressBuffer.slice(-1000);
		});
		child.on('error', error => { clearTimeout(timer); reject(error); });
		child.on('close', code => {
			clearTimeout(timer);
			if (timedOut) { reject(new Error('Model loading or code generation exceeded the time or output limit.')); return; }
			try {
				const result = JSON.parse(stdout);
				if (code !== 0 || result.error) reject(new Error(result.error || stderr || 'Code generation failed.'));
				else resolveResult(result);
			} catch (error) {
				reject(error instanceof SyntaxError ? new Error(stderr || 'Code generator returned no result.') : error);
			}
		});
		child.stdin.end(JSON.stringify({ model, language, prompt }));
	});
}

function closeSession(session) {
	clearTimeout(session.idleTimer);
	sessions.delete(session.id);
	session.child.stdin.end();
	setTimeout(() => session.child.kill(), 2000).unref();
}

function keepSessionAlive(session) {
	clearTimeout(session.idleTimer);
	session.idleTimer = setTimeout(() => closeSession(session), 10 * 60_000);
	session.idleTimer.unref();
}

function sendToSession(session, message) {
	if (session.pending) throw new Error('A cell is already running.');
	keepSessionAlive(session);
	return new Promise((resolveResult, reject) => {
		const timer = setTimeout(() => {
			session.pending = null;
			closeSession(session);
			reject(new Error('Cell execution timed out. Start a new session.'));
		}, 30_000);
		session.pending = {
			resolve: result => {
				clearTimeout(timer);
				if (result.error) reject(new Error(result.error));
				else resolveResult(result);
			},
			reject: error => { clearTimeout(timer); reject(error); },
		};
		if (message) session.child.stdin.write(JSON.stringify(message) + '\n');
	});
}

async function startSession() {
	if (sessions.size >= 4) throw new Error('Too many open notebooks. Close one before starting another.');
	const venv = resolve(root, '.venv', 'Scripts', 'python.exe');
	const python = process.env.NOTEBOOK_PYTHON || (existsSync(venv) ? venv : 'py');
	const child = spawn(python, [resolve(root, 'scripts', 'notebook-session.py')], {
		cwd: root, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
	});
	const session = { id: randomUUID(), child, pending: null, buffer: '', idleTimer: null };
	sessions.set(session.id, session);
	child.stdout.on('data', chunk => {
		session.buffer += chunk;
		if (session.buffer.length > 1_000_000) { closeSession(session); child.kill(); return; }
		while (session.buffer.includes('\n')) {
			const newline = session.buffer.indexOf('\n');
			const line = session.buffer.slice(0, newline);
			session.buffer = session.buffer.slice(newline + 1);
			try {
				const pending = session.pending;
				session.pending = null;
				pending?.resolve(JSON.parse(line));
			} catch {
				closeSession(session);
				child.kill();
			}
		}
	});
	child.stderr.on('data', () => { });
	child.on('error', error => {
		session.pending?.reject(error);
		session.pending = null;
		closeSession(session);
	});
	child.on('close', () => {
		session.pending?.reject(new Error('Notebook kernel stopped. Start a new session.'));
		session.pending = null;
		clearTimeout(session.idleTimer);
		sessions.delete(session.id);
	});
	try {
		await sendToSession(session);
		return { sessionId: session.id };
	} catch (error) {
		closeSession(session);
		throw error;
	}
}

function runNotebook(notebook) {
	return new Promise((resolveResult, reject) => {
		const venv = resolve(root, '.venv', 'Scripts', 'python.exe');
		const python = process.env.NOTEBOOK_PYTHON || (existsSync(venv) ? venv : 'py');
		const child = spawn(python, [resolve(root, 'scripts', 'run-notebook.py')], {
			cwd: root, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
		});
		let stdout = '';
		let stderr = '';
		let stopped = false;
		const timer = setTimeout(() => { stopped = true; child.kill(); }, 90_000);
		child.stdout.on('data', chunk => {
			stdout += chunk;
			if (stdout.length > 1_000_000) { stopped = true; child.kill(); }
		});
		child.stderr.on('data', chunk => {
			stderr += chunk;
			if (stderr.length > 100_000) { stopped = true; child.kill(); }
		});
		child.on('error', error => { clearTimeout(timer); reject(error); });
		child.on('close', code => {
			clearTimeout(timer);
			if (stopped) { reject(new Error('Notebook exceeded the execution limit.')); return; }
			try {
				const result = JSON.parse(stdout);
				if (code !== 0 || result.error) reject(new Error(result.error || stderr || 'Notebook failed.'));
				else resolveResult(result);
			} catch (error) {
				reject(error instanceof SyntaxError ? new Error(stderr || 'Notebook runner returned no result.') : error);
			}
		});
		child.stdin.end(JSON.stringify(notebook));
	});
}

function runWsl(args) {
	return new Promise((resolve, reject) => {
		const child = spawn('wsl.exe', args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
		let stdout = '';
		let stderr = '';
		let finished = false;
		const timer = setTimeout(() => child.kill(), 30_000);

		function append(stream, chunk) {
			const text = chunk.toString();
			if (stdout.length + stderr.length + text.length > 256_000) {
				child.kill();
				return;
			}
			if (stream === 'stdout') stdout += text;
			else stderr += text;
		}

		child.stdout.on('data', chunk => append('stdout', chunk));
		child.stderr.on('data', chunk => append('stderr', chunk));
		child.on('error', error => {
			finished = true;
			clearTimeout(timer);
			reject(error);
		});
		child.on('close', code => {
			clearTimeout(timer);
			if (!finished) resolve({ stdout, stderr, code });
		});
	});
}

async function handle(route, body) {
	if (route === '/api/code/progress') {
		return { phase: !generatingCode ? 'idle' : codeProgressPhase ?? 'loading', progress: codeProgress };
	}
	if (route === '/api/code/generate') {
		if (typeof body?.model !== 'string' || !/^[\w.-]+\/[\w.-]+$/.test(body.model) ||
			typeof body.language !== 'string' || body.language.length > 40 ||
			typeof body.prompt !== 'string' || !body.prompt.trim() || body.prompt.length > 4_000) {
			throw new Error('Provide a Hugging Face model ID, language, and request under 4,000 characters.');
		}
		if (generatingCode) throw new Error('A code-generation request is already running.');
		generatingCode = true;
		codeProgressPhase = 'loading';
		codeProgress = null;
		try { return await generateCode(body.model, body.language, body.prompt); }
		finally { generatingCode = false; codeProgressPhase = null; codeProgress = null; }
	}
	if (route === '/api/dataframe/read') {
		const name = body?.name;
		const data = body?.data;
		if (typeof name !== 'string' || name.length > 255 || !/\.(csv|xlsx|json)$/i.test(name) ||
			typeof data !== 'string' || data.length > 6_700_000) {
			throw new Error('Choose a CSV, XLSX, or JSON file under 5 MB.');
		}
		return readDataFile(name, data);
	}
	if (route === '/api/image/device') return runImage(null, true);
	if (route === '/api/image/progress') {
		return { phase: !generatingImage ? 'idle' : imageProgress === null ? 'loading' : 'generating', progress: imageProgress };
	}
	if (route === '/api/image/generate') {
		if (typeof body?.prompt !== 'string' || !body.prompt.trim() || body.prompt.length > 500) {
			throw new Error('Enter a prompt of up to 500 characters.');
		}
		if (generatingImage) throw new Error('An image is already generating.');
		generatingImage = true;
		imageProgress = null;
		try { return await runImage(body.prompt); }
		finally { generatingImage = false; imageProgress = null; }
	}
	if (route === '/api/ollama/models' || route === '/api/ollama/chat') {
		const chat = route === '/api/ollama/chat';
		if (chat && (typeof body.model !== 'string' || !/^[\w.\-/:]+$/.test(body.model) ||
			!Array.isArray(body.messages) || body.messages.length > 30 ||
			!body.messages.every(message => ['user', 'assistant'].includes(message.role) &&
				typeof message.content === 'string' && message.content.length <= 8_000))) {
			throw new Error('Choose a model and enter a prompt.');
		}
		let response;
		try {
			response = await fetch(`http://127.0.0.1:11434/api/${chat ? 'chat' : 'tags'}`, {
				method: chat ? 'POST' : 'GET',
				headers: chat ? { 'Content-Type': 'application/json' } : undefined,
				body: chat ? JSON.stringify({ model: body.model, messages: body.messages, stream: false }) : undefined,
				signal: AbortSignal.timeout(chat ? 120_000 : 5_000),
			});
		} catch {
			throw new Error('Ollama is unavailable. Start ollama serve and install a model.');
		}
		const text = await response.text();
		if (text.length > 500_000) throw new Error('Ollama response is too large.');
		const result = JSON.parse(text);
		if (!response.ok) throw new Error(result.error || `Ollama returned ${response.status}.`);
		return chat ? { message: result.message?.content ?? '' } : { models: (result.models ?? []).map(model => model.name) };
	}
	if (route === '/api/notebook/start') return startSession();
	if (route === '/api/notebook/stop') {
		const session = sessions.get(body.sessionId);
		if (session) closeSession(session);
		return {};
	}
	if (route === '/api/notebook/cell') {
		const session = sessions.get(body.sessionId);
		if (!session) throw new Error('Notebook session expired. Run the cell again to start a new one.');
		if (typeof body.source !== 'string' || body.source.length > 100_000) throw new Error('Invalid cell source.');
		return sendToSession(session, { source: body.source });
	}
	if (route === '/api/notebook/run') {
		if (typeof body !== 'object' || body === null || body.nbformat !== 4 || !Array.isArray(body.cells)) {
			throw new Error('Choose a valid version 4 Jupyter notebook.');
		}
		return runNotebook(body);
	}
	if (route === '/api/terminal/open') {
		const folder = body.folder;
		if (typeof folder !== 'string' || !/^[a-zA-Z]:[\\/]/.test(folder) || !win32.isAbsolute(folder)) {
			throw new Error('Enter an absolute Windows folder path, such as C:\\Projects\\my-folder.');
		}
		if (!(await stat(folder)).isDirectory()) throw new Error('That path is not a folder.');
		const result = await runWsl(['--exec', 'wslpath', '-a', '-u', folder]);
		if (result.code !== 0) throw new Error('WSL could not open this folder. Install a Linux distribution and check that wsl.exe starts successfully.');
		return { cwd: result.stdout.trim() };
	}

	if (route === '/api/terminal/run') {
		const { cwd, command } = body;
		if (typeof cwd !== 'string' || !cwd.startsWith('/') || typeof command !== 'string' || !command.trim() || command.length > 4096) {
			throw new Error('Open a folder and enter a command first.');
		}
		const marker = randomUUID();
		const script = 'cd -- "$1" || exit 1; eval "$2"; result=$?; printf "\\n%s%s\\n" "$3" "$PWD"; exit "$result"';
		const result = await runWsl(['--exec', 'bash', '-lc', script, 'bash', cwd, command, marker]);
		const markerIndex = result.stdout.lastIndexOf(`\n${marker}`);
		const nextCwd = markerIndex >= 0 ? result.stdout.slice(markerIndex + marker.length + 1).trimEnd() : cwd;
		return {
			cwd: nextCwd,
			stdout: markerIndex >= 0 ? result.stdout.slice(0, markerIndex) : result.stdout,
			stderr: result.stderr,
			code: result.code,
		};
	}
	throw new Error('Unknown terminal action.');
}

const server = createServer(async (request, response) => {
	const origin = request.headers.origin;
	if (!origins.has(origin) || request.headers.host !== `127.0.0.1:${port}`) {
		response.writeHead(403).end();
		return;
	}
	response.setHeader('Access-Control-Allow-Origin', origin);
	response.setHeader('Vary', 'Origin');
	response.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
	response.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Dino-Terminal');
	if (request.method === 'OPTIONS') {
		response.writeHead(204).end();
		return;
	}
	if (request.method === 'GET' && request.url === '/api/health') {
		response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({ service: 'dino-studio', apiVersion: 4 }));
		return;
	}
	if (request.method !== 'POST' || request.headers['x-dino-terminal'] !== '1' || !request.headers['content-type']?.startsWith('application/json')) {
		response.writeHead(405).end();
		return;
	}
	try {
		let text = '';
		for await (const chunk of request) {
			text += chunk;
			if (text.length > 7_000_000) throw new Error('Request is too large.');
		}
		const result = await handle(request.url, JSON.parse(text));
		response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(result));
	} catch (error) {
		response.writeHead(400, { 'Content-Type': 'application/json' }).end(JSON.stringify({ error: error.message }));
	}
});
server.on('error', error => {
	if (error.code === 'EADDRINUSE' && port < 4320) {
		port++;
		server.listen(port, '127.0.0.1');
	} else {
		console.error(`Unable to start local API: ${error.message}`);
		process.exitCode = 1;
	}
});
server.on('listening', () => console.log(`Local API listening on http://127.0.0.1:${port}`));
server.listen(port, '127.0.0.1');