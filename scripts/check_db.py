import sqlite3

con = sqlite3.connect('data/cache/t9fox.db')
tables = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for t in tables:
    name = t[0]
    count = con.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
    print(name + ': ' + str(count) + ' rows')
    if count > 0:
        cols = [d[0] for d in con.execute('SELECT * FROM ' + name + ' LIMIT 1').description]
        print('  columns: ' + ', '.join(cols))
        row = con.execute('SELECT * FROM ' + name + ' ORDER BY rowid DESC LIMIT 1').fetchone()
        print('  latest : ' + str(tuple(row)))
con.close()
