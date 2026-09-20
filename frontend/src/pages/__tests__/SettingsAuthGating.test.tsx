import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Settings } from "../Settings";

const apiMock = vi.hoisted(() => ({
  getLLMSettings: vi.fn(),
  getDataSourceSettings: vi.fn(),
  getChannelStatus: vi.fn(),
  listLLMModels: vi.fn(),
  startChannels: vi.fn(),
  stopChannels: vi.fn(),
  updateLLMSettings: vi.fn(),
  updateDataSourceSettings: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: apiMock,
    isAuthRequiredError: vi.fn(() => false),
  };
});

vi.mock("@/lib/apiAuth", async () => {
  const actual = await vi.importActual<typeof import("@/lib/apiAuth")>("@/lib/apiAuth");
  return {
    ...actual,
    getApiAuthKey: vi.fn(() => ""),
    setApiAuthKey: vi.fn(),
  };
});

function llmSettings() {
  return {
    provider: "openrouter",
    model_name: "deepseek/deepseek-v3.2",
    base_url: "https://openrouter.ai/api/v1",
    api_key_env: "OPENROUTER_API_KEY",
    api_key_configured: false,
    api_key_required: true,
    temperature: 0.1,
    timeout_seconds: 120,
    max_retries: 2,
    reasoning_effort: "",
    sse_timeout_seconds: 300,
    env_path: "agent/.env",
    providers: [],
  };
}

function dataSourceSettings() {
  return {
    tushare_token_configured: false,
    baostock_supported: true,
    baostock_installed: true,
    baostock_message: "BaoStock available",
    env_path: "agent/.env",
  };
}

function channelStatus() {
  return {
    running: false,
    inbound_queue: 0,
    outbound_queue: 0,
    session_count: 0,
    channels: {},
  };
}

describe("Settings capability gating (D19)", () => {
  beforeEach(async () => {
    apiMock.getLLMSettings.mockResolvedValue(llmSettings());
    apiMock.getDataSourceSettings.mockResolvedValue(dataSourceSettings());
    apiMock.getChannelStatus.mockResolvedValue(channelStatus());
  });

  afterEach(async () => {
    const { useAuthStore } = await import("@/stores/auth");
    useAuthStore.setState({ userAuth: false, modeLoaded: true, token: null, username: null, role: null, displayName: null });
  });

  it("keeps the shared-key section while user auth is off", async () => {
    render(<Settings />);

    expect(await screen.findByText("Local API access")).toBeInTheDocument();
    expect(screen.getByText("Server API key")).toBeInTheDocument();
  });

  it("hides the shared-key section once user auth is on", async () => {
    const { useAuthStore } = await import("@/stores/auth");
    useAuthStore.getState().setUserAuth(true);

    render(<Settings />);

    expect(await screen.findByText("LLM Settings")).toBeInTheDocument();
    expect(screen.queryByText("Local API access")).not.toBeInTheDocument();
    expect(screen.queryByText("Server API key")).not.toBeInTheDocument();
  });
});