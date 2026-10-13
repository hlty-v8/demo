export interface ProductGalleryProps {
  imageUrl: string | null;
  alt: string;
}

export function ProductGallery({ imageUrl, alt }: ProductGalleryProps) {
  return (
    <div className="gallery">
      {imageUrl ? (
        <img className="gallery__image" src={imageUrl} alt={alt} width={600} height={600} />
      ) : (
        <div
          className="gallery__placeholder"
          role="img"
          aria-label="Select a colour and size to see this variant's image"
        />
      )}
    </div>
  );
}
