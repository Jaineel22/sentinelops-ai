import { render, screen, waitFor } from "@testing-library/react";
import { AuthGuard } from "@/app/components/AuthGuard";

const replace = jest.fn();
jest.mock("@/app/lib/auth", () => ({
  isAuthenticated: jest.fn(),
  fetchMe: jest.fn(),
}));
import { fetchMe, isAuthenticated } from "@/app/lib/auth";
jest.mock("next/navigation", () => ({
  usePathname: () => "/incidents",
  useRouter: () => ({ replace }),
}));

describe("AuthGuard", () => {
  beforeEach(() => {
    replace.mockReset();
    jest.restoreAllMocks();
  });

  it("redirects unauthenticated users", async () => {
    (isAuthenticated as jest.Mock).mockReturnValue(false);
    render(<AuthGuard>secret</AuthGuard>);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login?next=%2Fincidents"));
    expect(screen.getByText("Checking session…")).toBeInTheDocument();
  });

  it("renders children after validating the session", async () => {
    (isAuthenticated as jest.Mock).mockReturnValue(true);
    (fetchMe as jest.Mock).mockResolvedValue({
      username: "alice",
      role: "viewer",
      disabled: false,
    });
    render(<AuthGuard>secret</AuthGuard>);
    expect(await screen.findByText("secret")).toBeInTheDocument();
  });

  it("shows a loading state while validation is pending", () => {
    (isAuthenticated as jest.Mock).mockReturnValue(true);
    (fetchMe as jest.Mock).mockReturnValue(new Promise(() => undefined));
    render(<AuthGuard>secret</AuthGuard>);
    expect(screen.getByText("Checking session…")).toBeInTheDocument();
  });
});
