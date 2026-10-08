// TEST ONLY: enables Shell.Eval / Screenshot D-Bus in a throwaway headless shell.
function init() { return { enable() { global.context.unsafe_mode = true; }, disable() { global.context.unsafe_mode = false; } }; }
