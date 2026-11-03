"""Models & sources: page stub (title and empty state); the full page lands in its own task."""
from lib import console

console.sync_node_param()
console.page("Models & sources", "Everything known about one node.", ['lineage_latest', 'node_executions', 'test_results'])
console.empty("Nothing to show yet.")
