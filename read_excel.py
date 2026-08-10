import pandas as pd

df = pd.read_excel('test_attributes.xlsx')
print(df.head())
print("Columns:", df.columns.tolist())
print(f"Total rows: {len(df)}")
