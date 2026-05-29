from neo4j import GraphDatabase

driver = GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j', 'Chennai@123'))
session = driver.session()

# Check Customer node properties
print('✓ Customer node structure:')
result = session.run('MATCH (cu:Customer) RETURN properties(cu) LIMIT 1')
record = result.single()
if record:
    props = dict(record[0])
    print('  Properties:', list(props.keys()))
    print('  Sample:', props)

# Check State node properties
print('\n✓ State node structure:')
result = session.run('MATCH (s:State) RETURN properties(s)')
states = []
for record in result:
    states.append(dict(record[0])['name'])
print(f'  States loaded: {sorted(states)}')

# Check Industry structure
print('\n✓ Industry structure:')
result = session.run('MATCH (i:Industry) RETURN count(i) as cnt')
industry_count = result.single()['cnt']

result = session.run('MATCH (i:Industry) RETURN properties(i) LIMIT 1')
record = result.single()
if record:
    props = dict(record[0])
    print(f'  Total industries: {industry_count}')
    print(f'  Properties: {list(props.keys())}')

# Get relationship properties
print('\n✓ Relationship structure:')
result = session.run("""
    MATCH (c:Carrier)-[r:LICENSED_IN]->(s:State)
    RETURN keys(r) as rel_keys
    LIMIT 1
""")
record = result.single()
if record:
    print(f'  LICENSED_IN keys: {record["rel_keys"] if record["rel_keys"] else "NONE"}')

# Check if carrier has pricing data
print('\n✓ Carrier pricing integrity:')
result = session.run("""
    MATCH (c:Carrier)
    RETURN c.name, c.pricing_rows, c.complaint_ratio_nat
    LIMIT 5
""")
for record in result:
    print(f'  {record["c.name"]}: {record["c.pricing_rows"]} pricing rows, complaint_ratio={record["c.complaint_ratio_nat"]}')

# Verify source grounding - check where data comes from
print('\n✓ Data sources in graph (sample NAIC properties):')
result = session.run("""
    MATCH (c:Carrier)
    RETURN c.name, c.naic_code, c.am_best, c.complaint_ratio_nat
    LIMIT 3
""")
for record in result:
    print(f'  {record["c.name"]}:')
    print(f'    - NAIC Code: {record["c.naic_code"]}')
    print(f'    - A.M.Best Rating: {record["c.am_best"]}')
    print(f'    - NAIC Complaint Ratio: {record["c.complaint_ratio_nat"]}')

session.close()
driver.close()
