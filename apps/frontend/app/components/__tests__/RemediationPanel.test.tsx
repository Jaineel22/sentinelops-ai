import { render, screen, waitFor } from "@testing-library/react";
import { RemediationPanel } from "@/app/components/RemediationPanel";
import { remediations } from "@/app/lib/api";
import { hasRole } from "@/app/lib/auth";

jest.mock("@/app/lib/api", () => ({
  remediations: { forIncident: jest.fn(), approve: jest.fn(), reject: jest.fn(), execute: jest.fn() },
}));
jest.mock("@/app/lib/auth", () => ({ hasRole: jest.fn() }));

const row = (status: "PENDING_APPROVAL" | "APPROVED") => ({
  remediation_id: "r1",
  incident_id: "i1",
  status,
  action_type: "RESTART_SERVICE",
  risk_level: "HIGH",
  target: { service_name: "orders", environment: "dev" },
  expires_at: "2026-01-01T00:00:00Z",
  reason: "restart",
  expected_effect: "healthy",
  policy: { outcome: "ALLOW", reason_codes: [] },
  approval: null,
  execution: null,
  verification: null,
});

describe("RemediationPanel RBAC", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (remediations.forIncident as jest.Mock).mockResolvedValue({ remediations: [], count: 0 });
  });

  it.each([["approver"], ["admin"]])("shows approval controls", async () => {
    (hasRole as jest.Mock).mockReturnValue(true);
    (remediations.forIncident as jest.Mock).mockResolvedValue({
      remediations: [row("PENDING_APPROVAL")],
      count: 1,
    });
    render(<RemediationPanel incidentId="i1" />);
    expect(await screen.findByText("Approve")).toBeInTheDocument();
    expect(screen.getByText("Reject")).toBeInTheDocument();
  });

  it("hides approval controls for viewers", async () => {
    (hasRole as jest.Mock).mockReturnValue(false);
    (remediations.forIncident as jest.Mock).mockResolvedValue({
      remediations: [row("PENDING_APPROVAL")],
      count: 1,
    });
    render(<RemediationPanel incidentId="i1" />);
    await waitFor(() => expect(screen.getByText("approver")).toBeInTheDocument());
    expect(screen.queryByText("Approve")).not.toBeInTheDocument();
  });

  it("shows execute control for approved remediations", async () => {
    (hasRole as jest.Mock).mockReturnValue(true);
    (remediations.forIncident as jest.Mock).mockResolvedValue({
      remediations: [row("APPROVED")],
      count: 1,
    });
    render(<RemediationPanel incidentId="i1" />);
    expect(await screen.findByText("Preview execution")).toBeInTheDocument();
  });

  it("renders loading and error states", async () => {
    (hasRole as jest.Mock).mockReturnValue(false);
    let reject!: (error: Error) => void;
    (remediations.forIncident as jest.Mock).mockReturnValue(
      new Promise((_, r) => {
        reject = r;
      }),
    );
    render(<RemediationPanel incidentId="i1" />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    reject(new Error("backend unavailable"));
    expect(await screen.findByText("backend unavailable")).toBeInTheDocument();
  });
});
