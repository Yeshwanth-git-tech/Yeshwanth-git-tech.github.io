from neo4j import GraphDatabase

driver = GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j', 'Chennai@123'))
session = driver.session()

# Check actual properties
result = session.run('MATCH (c:Carrier) RETURN properties(c) LIMIT 1')
props = result.single()[0]
print('Carrier properties:', list(props.keys()))

# Check a full carrier record
result = session.run("MATCH (c:Carrier) WHERE c.name = 'The Hartford' RETURN properties(c)")
record = result.single()
if record:
    carrier = dict(record[0])
    print('\nThe Hartford record:')
    for k, v in carrier.items():
        print(f'  {k}: {v}')

# Check licensed_in relationships sample
print('\n✓ Sample LICENSED_IN relationships:')
result = session.run("""
    MATCH (c:Carrier)-[r:LICENSED_IN]->(s:State)
    RETURN c.name, s.name, r.status
    LIMIT 5
""")
for record in result:
    print(f'  {record["c.name"]} -> {record["s.name"]} ({record["r.status"]})')

# Check specializes_in relationships
print('\n✓ Sample SPECIALIZES_IN relationships:')
result = session.run("""
    MATCH (c:Carrier)-[r:SPECIALIZES_IN]->(i:Industry)
    RETURN c.name, i.name, r.appetite_level
    LIMIT 5
""")
for record in result:
    print(f'  {record["c.name"]} -> {record["i.name"]} (appetite: {record["r.appetite_level"]})')

# Check customer-industry match
print('\n✓ Sample Customer data:')
result = session.run("""
    MATCH (cu:Customer)
    RETURN cu.name, cu.industry, cu.state
    LIMIT 3
""")
for record in result:
    print(f'  {record["cu.name"]}: {record["cu.industry"]} in {record["cu.state"]}')

session.close()
driver.close()
