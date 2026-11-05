let serviceUrl: Promise<string> | undefined;

async function discoverService(): Promise<string> {
	const candidates = Array.from({ length: 20 }, (_, index) => {
		const url = `http://127.0.0.1:${4301 + index}`;
		return fetch(`${url}/api/health`, { signal: AbortSignal.timeout(1500) }).then(async response => {
			const health = await response.json();
			if (!response.ok || health.service !== 'dino-studio' || health.apiVersion !== 4) throw new Error('Incompatible local API.');
			return url;
		});
	});
	try {
		return await Promise.any(candidates);
	} catch {
		throw new Error('Local service unavailable. Start npm start.');
	}
}

export async function fetchLocalApi(path: string, body: object, keepalive = false): Promise<Response> {
	const url = await (serviceUrl ??= discoverService().catch(error => {
		serviceUrl = undefined;
		throw error;
	}));
	try {
		return await fetch(`${url}${path}`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json', 'X-Dino-Terminal': '1' },
			body: JSON.stringify(body),
			keepalive,
		});
	} catch {
		serviceUrl = undefined;
		throw new Error('Local service unavailable. Restart npm start.');
	}
}