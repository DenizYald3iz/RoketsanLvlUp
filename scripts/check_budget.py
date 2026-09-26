from s2agent.budget import key_info

i = key_info()
print(f"spend={i.get('spend')}  max_budget={i.get('max_budget')}")
