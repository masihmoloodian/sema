import { validateToken } from "./jwt";

export function requireAuth(req: any, res: any, next: any): void {
  const header = req.headers["authorization"] || "";
  const token = header.replace("Bearer ", "");
  if (!token) {
    res.status(401).json({ error: "Unauthorized" });
    return;
  }
  validateToken(token)
    .then((user) => {
      req.user = user;
      next();
    })
    .catch(() => res.status(401).json({ error: "Invalid token" }));
}

export function optionalAuth(req: any, res: any, next: any): void {
  const header = req.headers["authorization"] || "";
  const token = header.replace("Bearer ", "");
  if (!token) {
    next();
    return;
  }
  validateToken(token)
    .then((user) => {
      req.user = user;
      next();
    })
    .catch(() => next());
}
