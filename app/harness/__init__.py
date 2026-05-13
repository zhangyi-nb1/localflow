"""Harness Kernel — the project core.

Modules in this package enforce the LocalFlow execution protocol:

    Inspect -> Plan -> RiskCheck -> DryRun -> Approve -> Checkpoint
            -> Execute -> Verify -> Report

The model is never trusted to perform side effects. All real changes
flow through these modules.
"""
