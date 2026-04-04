// Shared mutable state — imported by modules that need cross-module variables.
export const state = {
  sessionPollTimer: null,
  dlcBrowsePath: null,
  dlcEngine: "pytorch",
  dlcTrainingActive: false,
  currentRoot: "",
  userDataDir: null,
  dataDir: null,
  currentProjectId: "",
  pollTimer: null,
};
