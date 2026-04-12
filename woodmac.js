export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') { res.status(204).end(); return; }

  const { path, ...params } = req.query;
  if (!path) { res.status(400).json({ error: 'Missing path' }); return; }

  const qs = new URLSearchParams(params).toString();
  const url = `https://data.woodmac.com${path}${qs ? '?' + qs : ''}`;

  try {
    const upstream = await fetch(url, {
      headers: { 'apikey': process.env.WM_API_KEY, 'Accept': 'application/json' }
    });
    const data = await upstream.json();
    res.status(upstream.status).json(data);
  } catch (e) {
    res.status(502).json({ error: e.message });
  }
}
