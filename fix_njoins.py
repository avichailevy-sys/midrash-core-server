import sqlite3
c = sqlite3.connect('midrash_core.db')
c.execute("""UPDATE manuscripts SET n_joins =
    (SELECT COUNT(DISTINCT join_id) FROM join_members
     WHERE join_members.alma = manuscripts.alma)""")
c.commit()
print("manuscripts with joins:",
      c.execute("SELECT COUNT(*) FROM manuscripts WHERE n_joins>0").fetchone()[0])
c.close()
