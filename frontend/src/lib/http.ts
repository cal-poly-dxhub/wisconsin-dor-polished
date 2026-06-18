import ky from 'ky';
import { getIdToken } from './auth';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL!;
if (!API_BASE_URL) throw new Error('NEXT_PUBLIC_API_BASE_URL is not set');

export const http = ky.create({
  prefixUrl: API_BASE_URL.replace(/\/+$/, ''),
  hooks: {
    beforeRequest: [
      async (req: Request) => {
        const newReq = new Request(req, {
          cache: 'no-store',
          next: { revalidate: 0 },
        });
        newReq.headers.set('Content-Type', 'application/json');

        const token = await getIdToken();
        if (token) {
          newReq.headers.set('Authorization', `Bearer ${token}`);
        }

        return newReq;
      },
    ],
    afterResponse: [
      async (_req: Request, _opt: unknown, res: Response) => {
        const ct = res.headers.get('content-type') || '';
        if (!ct.includes('application/json')) {
          const responseText = await res.text();
          throw new Error(
            `Unexpected content-type: ${ct}. Response: ${responseText}`
          );
        }
      },
    ],
  },
  timeout: 30_000,
  retry: {
    limit: 2,
    methods: ['get', 'put', 'head', 'delete', 'options', 'trace'],
  },
});
