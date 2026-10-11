import argparse, sqlite3, secrets, time, re
from main import connect, digest

p=argparse.ArgumentParser(description='Private TV management CLI')
s=p.add_subparsers(dest='command',required=True)
a=s.add_parser('add-user');a.add_argument('username');a.add_argument('--password',required=False)
a=s.add_parser('disable-user');a.add_argument('username')
a=s.add_parser('add-channel');a.add_argument('id',type=int);a.add_argument('name');a.add_argument('folder');a.add_argument('--category',default='Personal');a.add_argument('--logo',default='')
s.add_parser('list-channels');s.add_parser('list-users')
a=p.parse_args()
with connect() as db:
    if a.command=='add-user':
        if not re.fullmatch('[A-Za-z0-9_-]{3,40}',a.username):p.error('Username must be 3-40 safe characters')
        pw=a.password or secrets.token_urlsafe(24)
        salt=secrets.token_hex(16)
        db.execute('INSERT INTO users(username,salt,password_hash,created_at,disabled) VALUES(?,?,?,?,0) ON CONFLICT(username) DO UPDATE SET salt=excluded.salt,password_hash=excluded.password_hash,disabled=0',(a.username,salt,digest(pw,salt),int(time.time())))
        print('Username:',a.username);print('Password:',pw)
    elif a.command=='disable-user':
        db.execute('UPDATE users SET disabled=1 WHERE username=?',(a.username,));print('Disabled',a.username)
    elif a.command=='add-channel':
        if not re.fullmatch('[A-Za-z0-9_-]+',a.folder):p.error('Folder must use alphanumerics, hyphens or underscores')
        db.execute('INSERT INTO channels(id,name,category,folder,logo) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,category=excluded.category,folder=excluded.folder,logo=excluded.logo',(a.id,a.name,a.category,a.folder,a.logo))
        print('Added channel',a.id,a.name)
    elif a.command=='list-channels':
        for row in db.execute('SELECT * FROM channels'): print(dict(row))
    elif a.command=='list-users':
        for row in db.execute('SELECT username,disabled,created_at FROM users'): print(dict(row))
    db.commit()
