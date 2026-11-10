import type { Plugin } from "@opencode-ai/plugin"

// Deliberately test-only: this plugin never intercepts production VT tools.
export const DiscoveryCanary: Plugin = async () => ({
  "tool.execute.before": async (input, output) => {
    if (input.tool === "probe_blocked") {
      throw new Error("discovery-canary-blocked")
    }
    if (input.tool === "probe_echo") {
      output.args.session_id = input.sessionID
    }
  },
})
