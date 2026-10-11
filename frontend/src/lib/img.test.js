import {galleryImages, firstImage, isSizeTableImage} from './img';
test('separates old unmarked size documents and video from sellable product photos',()=>{
 const chart='/api/upload/files/product-olcu-kredisiz-a1b2c3d4.jpg';
 const p={images:[chart,{url:'/chart.jpg',is_size_table:true},{url:'/video.mp4',is_video:true},{url:'/front.jpg'},'/front.jpg','/back.jpg']};
 expect(isSizeTableImage(chart)).toBe(true);
 expect(isSizeTableImage('/api/files/product-olcu-oto-a1b2c3d4.jpg')).toBe(true);
 expect(galleryImages(p)).toEqual(['/front.jpg','/back.jpg']);
 expect(firstImage(p)).toBe('/front.jpg');
 expect(isSizeTableImage('/olcu-dikisli-kazak.jpg')).toBe(false);
});
