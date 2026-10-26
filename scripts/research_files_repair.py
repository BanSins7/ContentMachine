"""Audit or quarantine orphaned PSI file blobs after crashes (local-only).

Usage: python -m scripts.research_files_repair PROJECT_ID [--quarantine]
Never deletes versions referenced by PostgreSQL; default dry-run.
"""
import argparse
import json
from intelligence.project_sources.file_library import repair_scan

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_id')
    parser.add_argument('--quarantine',action='store_true',help='Move orphans older than 10 minutes to quarantine')
    args=parser.parse_args()
    print(json.dumps(repair_scan(args.project_id,quarantine=args.quarantine),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
