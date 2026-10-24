import { createServer } from 'node:http';
import { connect, createServer as createTcpServer } from 'node:net';

if (process.env.FIXTURE_MODE === 'ui-gateway') {
	// Fixed upstream only: this does not provide an outbound proxy to n8n.
	createTcpServer((client) => {
		const upstream = connect({ host: 'n8n', port: 5678 });
		client.on('error', () => upstream.destroy());
		upstream.on('error', () => client.destroy());
		client.on('close', () => upstream.destroy());
		upstream.on('close', () => client.destroy());
		client.pipe(upstream).pipe(client);
	}).listen(5678, '0.0.0.0');
} else {
	createServer((request, response) => {
		const route = request.url ?? '';
		const project = route.endsWith('b') ? 'b' : 'a';
		const secret = process.env[`FIXTURE_${project.toUpperCase()}_SECRET`];
		const expected = `Basic ${Buffer.from(`${project}:${secret}`).toString('base64')}`;
		response.setHeader('Content-Type', 'application/json');
		if (request.headers.authorization !== expected) {
			response
				.writeHead(401)
				.end(JSON.stringify({ error: 'Fixture credentials do not match' }));
			return;
		}
		response.writeHead(route.startsWith('/fail-') ? 500 : 200);
		response.end(JSON.stringify({ project, authenticated: true }));
	}).listen(8080, '0.0.0.0');
}
