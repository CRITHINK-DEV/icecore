"""Incidents: page stub (title and empty state); the full page lands in its own task."""
from lib import console

console.page("Incidents", "What is open, what is acknowledged, what is resolved.", ['alerts_feed'])
console.empty("Nothing to show yet.")
