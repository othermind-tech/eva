# core/daemons/__init__.py
#
# G-008: Daemon Agent subsystem
#
# A daemon agent is an autonomous, schedule-driven agent that runs on a
# configured interval (or is manually triggered via POST /daemons/{name}/trigger).
# Each daemon run executes a full AgentEngine pass, has access to the tool
# registry, and shares the inter-agent messaging system (G-007).
#
# Submodules:
#   registry  — DB-backed CRUD for DaemonConfig rows
#   runner    — executes a single daemon run (AgentEngine invocation)
#   scheduler — async background loop that fires daemons on schedule
