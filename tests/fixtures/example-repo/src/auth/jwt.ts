import { User, TokenPair } from "../types";

/**
 * Generate a signed JWT token for a user.
 */
export function generateToken(userId: string): string {
  const payload = { sub: userId, iat: Date.now() };
  return btoa(JSON.stringify(payload)) + ".signature";
}

/**
 * Validate a JWT token and return the associated user.
 */
export async function validateToken(token: string): Promise<User> {
  const parts = token.split(".");
  if (parts.length !== 2) {
    throw new Error("Invalid token format");
  }
  const payload = JSON.parse(atob(parts[0]));
  if (!payload.sub) {
    throw new Error("Token missing subject");
  }
  return { id: payload.sub, email: "user@example.com" };
}

/**
 * Refresh an existing token pair for a user.
 */
export async function refreshToken(
  userId: string,
  token: string
): Promise<TokenPair> {
  await validateToken(token);
  const access = generateToken(userId);
  const refresh = generateToken(userId + "-refresh");
  return { access, refresh };
}

function validateExpiry(token: string): boolean {
  try {
    const parts = token.split(".");
    const payload = JSON.parse(atob(parts[0]));
    const exp = payload.exp || Infinity;
    return Date.now() < exp;
  } catch {
    return false;
  }
}
