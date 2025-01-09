import subprocess
import csv
import os
from datetime import datetime
from tqdm import tqdm  # pip install tqdm

def get_git_config_value(key):
    """Get value from git config"""
    try:
        return subprocess.check_output(['git', 'config', '--get', key]).decode('utf-8').strip()
    except:
        return None

def get_commit_details(commit_hash):
    """Get detailed information about a specific commit"""
    try:
        # Get commit message and other metadata
        format_string = '--pretty=format:%H,%an,%ae,%at,%s'
        commit_info = subprocess.check_output(
            ['git', 'show', commit_hash, format_string],
            stderr=subprocess.DEVNULL
        ).decode('utf-8').split('\n')[0].split(',')

        # Get code differences
        diff = subprocess.check_output(
            ['git', 'show', '--stat', commit_hash],
            stderr=subprocess.DEVNULL
        ).decode('utf-8')

        # Get branch information
        branch_info = subprocess.check_output(
            ['git', 'branch', '--contains', commit_hash],
            stderr=subprocess.DEVNULL
        ).decode('utf-8').strip()

        return {
            'hash': commit_info[0],
            'author': commit_info[1],
            'email': commit_info[2],
            'date': datetime.fromtimestamp(int(commit_info[3])).strftime('%Y-%m-%d %H:%M:%S'),
            'message': commit_info[4],
            'diff': diff,
            'branches': branch_info.replace('* ', '').replace('\n', '; ')
        }
    except Exception as e:
        print(f"Error processing commit {commit_hash}: {str(e)}")
        return None

def main():
    # Get git username and email for filtering (optional)
    git_username = get_git_config_value('user.name')
    git_email = get_git_config_value('user.email')
    
    print(f"Fetching commits for {git_username} ({git_email})")

    # Get all commits including merges
    try:
        commits = subprocess.check_output(
            ['git', 'log', '--all', '--format=%H'],
            stderr=subprocess.DEVNULL
        ).decode('utf-8').strip().split('\n')
    except Exception as e:
        print(f"Error getting commit list: {str(e)}")
        return

    # Prepare CSV file
    csv_file = 'git_history.csv'
    headers = ['Hash', 'Author', 'Email', 'Date', 'Message', 'Branches', 'Code Changes']

    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        
        # Add progress bar
        for commit in tqdm(commits, desc="Processing commits"):
            details = get_commit_details(commit)
            if details:
                writer.writerow({
                    'Hash': details['hash'],
                    'Author': details['author'],
                    'Email': details['email'],
                    'Date': details['date'],
                    'Message': details['message'],
                    'Branches': details['branches'],
                    'Code Changes': details['diff']
                })
                print(f"Processed commit {details['hash'][:8]}")

    print(f"\nGit history has been exported to {csv_file}")

if __name__ == "__main__":
    main() 