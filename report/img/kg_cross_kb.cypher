MATCH p=(:CaseReport)-[:ALLEGES]->(:Crime)<-[:DEFINES]-(:Article) RETURN p LIMIT 10;
