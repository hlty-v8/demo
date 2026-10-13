/**
 * Wire types: exactly what the FastAPI backend sends over the network
 * (snake_case, integer cents). Kept separate from the domain model so a change
 * to the transport never ripples through the UI.
 */

export interface OptionValueDto {
  id: string;
  label: string;
}

export interface OptionDimensionDto {
  id: string;
  name: string;
  values: OptionValueDto[];
}

export interface SkuDto {
  id: string;
  options: Record<string, string>;
  price_cents: number;
  available_qty: number;
  in_stock: boolean;
  image_url: string;
}

export interface ProductDto {
  id: string;
  name: string;
  description: string;
  currency: string;
  options: OptionDimensionDto[];
  skus: SkuDto[];
}

export interface CartItemDto {
  sku_id: string;
  quantity: number;
  unit_price_cents: number;
  line_total_cents: number;
  available_qty: number;
  image_url: string;
  options: Record<string, string>;
}

export interface CartDto {
  id: string;
  items: CartItemDto[];
  total_items: number;
  subtotal_cents: number;
}

export interface AddCartItemResponseDto {
  cart_id: string;
  item: CartItemDto;
  cart: CartDto;
}

export interface ApiErrorBodyDto {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
    request_id?: string;
  };
}
