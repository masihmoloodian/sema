type Handler = (req: any, res: any) => void;

export class Router {
  private routes: Map<string, Handler> = new Map();

  get(path: string, handler: Handler): void {
    this.routes.set(`GET:${path}`, handler);
  }

  post(path: string, handler: Handler): void {
    this.routes.set(`POST:${path}`, handler);
  }

  dispatch(method: string, path: string, req: any, res: any): void {
    const handler = this.routes.get(`${method}:${path}`);
    if (handler) {
      handler(req, res);
    } else {
      res.status(404).json({ error: "Not found" });
    }
  }
}
