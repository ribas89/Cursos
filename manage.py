"""Run offline inside the cursos container to manage accounts."""
import argparse
import getpass
from store import init_db, connect, change_password

p = argparse.ArgumentParser()
s = p.add_subparsers(dest='command', required=True)
s.add_parser('list-users')
r = s.add_parser('reset-password')
r.add_argument('email')
args = p.parse_args()
init_db()
if args.command == 'list-users':
    with connect() as db:
        for row in db.execute('SELECT id,email,created_at FROM users ORDER BY id'):
            print(row['id'],row['email'],row['created_at'])
elif args.command == 'reset-password':
    passwd = getpass.getpass('New password: ')
    confirm = getpass.getpass('Confirm password: ')
    if passwd != confirm or len(passwd) < 10:
        p.error('Passwords must match and contain at least 10 characters')
    if not change_password(args.email, passwd):
        p.error('Email not found')
    print('Password updated for', args.email)
