"""Overview: page stub (title and empty state); the full page lands in its own task."""
from lib import console

console.page("Overview", "Is the platform healthy right now.", ['invocations', 'test_results', 'alerts_feed'])
console.empty("Nothing to show yet.")
