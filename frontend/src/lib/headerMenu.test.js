import {categoryMenu} from './headerMenu';
test('keeps identically named categories inside their own gender root', () => {
 const menu=categoryMenu([
  {id:'w',name:'Kadın',slug:'kadin',parent_id:null},
  {id:'m',name:'Erkek',slug:'erkek',parent_id:null},
  {id:'wc',name:'Kazak',slug:'kadin-kazak',parent_id:'w'},
  {id:'mc',name:'Kazak',slug:'erkek-kazak',parent_id:'m'},
  {id:'hidden',name:'Eski',slug:'eski',parent_id:'w',is_active:false},
 ]);
 expect(menu.find(t=>t.id==='kadin').columns[0].items).toEqual([{name:'Kazak',link:'/kadin-kazak'}]);
 expect(menu.find(t=>t.id==='erkek').columns[0].items).toEqual([{name:'Kazak',link:'/erkek-kazak'}]);
});
