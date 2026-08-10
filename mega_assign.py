import pymongo
import os
from dotenv import load_dotenv

def main():
    load_dotenv(os.path.join('/Users/kdrgry/.gemini/antigravity/playground/ruby-nova/backend', '.env'))
    client = pymongo.MongoClient(os.getenv('MONGODB_URL', 'mongodb://localhost:27017'))
    db = client['test_database']

    products = list(db.products.find({}))
    updated_count = 0

    for p in products:
        name = p.get('name', '')
        current_attrs = p.get('attributes', [])
        
        attr_dict = {}
        for a in current_attrs:
            if isinstance(a, dict):
                key = a.get('type') or a.get('name')
                if key:
                    attr_dict[key] = a.get('value')
        
        # Guesses
        if 'Trençkot' in name:
            attr_dict.setdefault('Kumaş', 'Pamuk')
        if 'Jean' in name:
            attr_dict.setdefault('Kumaş', 'Denim')
        if 'Kazak' in name:
            attr_dict.setdefault('Kumaş', 'Triko')
        
        if 'Trençkot' in name: attr_dict.setdefault('Ürün Tipi', 'Trençkot')
        elif 'Jean' in name: attr_dict.setdefault('Ürün Tipi', 'Jean')
        elif 'Kazak' in name: attr_dict.setdefault('Ürün Tipi', 'Kazak')
        elif 'Ceket' in name or 'Blazer' in name: attr_dict.setdefault('Ürün Tipi', 'Ceket')
        elif 'Pantolon' in name: attr_dict.setdefault('Ürün Tipi', 'Pantolon')
        elif 'Gömlek' in name: attr_dict.setdefault('Ürün Tipi', 'Gömlek')
        elif 'T-Shirt' in name or 'Tişört' in name: attr_dict.setdefault('Ürün Tipi', 'T-Shirt')
        
        new_attrs = [{'type': k, 'name': k, 'value': v} for k, v in attr_dict.items()]
        
        if str(new_attrs) != str(current_attrs):
            try:
                db.products.update_one(
                    {'_id': p['_id']}, 
                    {'$set': {'attributes': new_attrs}}
                )
                updated_count += 1
            except Exception as e:
                print(f"Error updating {name}: {e}")

    print(f'Final Sync Done! Updated {updated_count} products.')

if __name__ == "__main__":
    main()
