import { clearToken, hasRole, isAuthenticated, login, logout } from "@/app/lib/auth";

const token = (role = "approver", sub = "alice") =>
  `eyJhbGciOiJIUzI1NiJ9.${btoa(JSON.stringify({ sub, role }))}.signature`;
const response = (body: unknown, ok = true) => ({
  ok,
  status: ok ? 200 : 401,
  json: async () => body,
});

describe("auth helpers", () => {
  beforeEach(() => {
    localStorage.clear();
    Object.defineProperty(global, "fetch", { configurable: true, writable: true, value: jest.fn() });
    jest.restoreAllMocks();
  });

  it("stores the token and returns the user on login", async () => {
    const fetchMock = jest
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(response({ access_token: token() }) as Response)
      .mockResolvedValueOnce(
        response({ username: "alice", role: "approver", disabled: false }) as Response,
      );
    await expect(login("alice", "secret")).resolves.toMatchObject({ role: "approver" });
    expect(localStorage.getItem("sentinelops.token")).toBe(token());
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rejects a failed login", async () => {
    jest.spyOn(global, "fetch").mockResolvedValue(response(null, false) as Response);
    await expect(login("alice", "bad")).rejects.toThrow("incorrect username or password");
    expect(isAuthenticated()).toBe(false);
  });

  it("clears the token and redirects on logout", () => {
    localStorage.setItem("sentinelops.token", token());
    expect(() => logout()).not.toThrow();
    expect(localStorage.getItem("sentinelops.token")).toBeNull();
  });

  it("reports authentication and role hierarchy", () => {
    expect(isAuthenticated()).toBe(false);
    expect(hasRole("viewer")).toBe(false);
    localStorage.setItem("sentinelops.token", token("approver"));
    expect(isAuthenticated()).toBe(true);
    expect(hasRole("viewer")).toBe(true);
    expect(hasRole("approver")).toBe(true);
    expect(hasRole("admin")).toBe(false);
  });

  it("clears a token explicitly", () => {
    localStorage.setItem("sentinelops.token", token());
    clearToken();
    expect(isAuthenticated()).toBe(false);
  });
});
