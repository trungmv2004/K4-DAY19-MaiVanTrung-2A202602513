MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC;
