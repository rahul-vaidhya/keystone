export type User = {
  id: string;
  org_id: string;
  email: string;
  name: string | null;
  role: "owner" | "admin" | "member";
  is_active: boolean;
  created_at: string;
};

export type TokenResponse = {
  access_token: string;
  token_type: string;
};

export type Organization = {
  id: string;
  name: string;
};

export type InviteOut = {
  user: User;
  org_id: string;
  invite_token: string;
};

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}
