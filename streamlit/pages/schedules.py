"""Schedules & freshness: page stub (title and empty state); the full page lands in its own task."""
from lib import console

console.page("Schedules & freshness", "Did the declared schedules and sources arrive on time.", ['sla_declared', 'silence_events', 'node_executions'])
console.empty("Nothing to show yet.")
