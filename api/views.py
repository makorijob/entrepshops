from django.db.models import Q, F, DecimalField, Value
from django.db.models.functions import Coalesce
from rest_framework import generics
from rest_framework.pagination import PageNumberPagination

from store.models import Category, Department, Product

from .serializers import (
    CategorySerializer, DepartmentSerializer,
    ProductDetailSerializer, ProductListSerializer,
)


class ProductPagination(PageNumberPagination):
    page_size = 24
    page_size_query_param = 'page_size'
    max_page_size = 100


SORT_CHOICES = {
    'newest':     '-created_at',
    'oldest':     'created_at',
    'price_asc':  'effective_price',
    'price_desc': '-effective_price',
    'name_asc':   'name',
    'name_desc':  '-name',
}


class ProductListView(generics.ListAPIView):
    """GET /api/v1/products/ — the grid endpoint.

    Query params:
      ?department=<slug>
      ?category=<slug>
      ?q=<search>
      ?type=sale|hire|all       (default: all)
      ?in_stock=1               (only show in-stock products)
      ?min_price=<number>
      ?max_price=<number>
      ?sort=newest|oldest|price_asc|price_desc|name_asc|name_desc
      ?page=<n>
      ?page_size=<n>
    """
    serializer_class = ProductListSerializer
    pagination_class = ProductPagination

    def get_queryset(self):
        qs = Product.objects.visible().select_related(
            'category', 'department'
        ).prefetch_related('images', 'variants')

        # ---- Filter params ----
        department = self.request.query_params.get('department')
        category = self.request.query_params.get('category')
        q = self.request.query_params.get('q')
        product_type = self.request.query_params.get('type', 'all')
        in_stock = self.request.query_params.get('in_stock') == '1'
        min_price = self.request.query_params.get('min_price')
        max_price = self.request.query_params.get('max_price')
        sort = self.request.query_params.get('sort', 'newest')

        if department:
            qs = qs.filter(department__slug=department)
        if category:
            qs = qs.filter(category__slug=category)
        if q:
            qs = qs.filter(
                Q(name__icontains=q) | Q(description__icontains=q)
            )

        # Type: sale / hire / all
        if product_type == 'sale':
            qs = qs.filter(is_rentable=False)
        elif product_type == 'hire':
            qs = qs.filter(is_rentable=True)

        # Availability
        if in_stock:
            from django.db.models import Exists, OuterRef
            from store.models import ProductVariant
            variant_in_stock = ProductVariant.objects.filter(
                product=OuterRef('pk'), is_active=True, stock__gt=0,
            )
            qs = qs.annotate(
                _has_stock_variant=Exists(variant_in_stock)
            ).filter(
                Q(_has_stock_variant=True) |
                Q(variants__isnull=True, stock__gt=0)
            ).distinct()

        # Price — use price for sale listings, daily_rate for hire listings.
        # "effective_price" is what we sort by as well.
        qs = qs.annotate(
            effective_price=Coalesce(
                'price', 'daily_rate',
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )

        if min_price:
            try:
                qs = qs.filter(effective_price__gte=float(min_price))
            except (TypeError, ValueError):
                pass
        if max_price:
            try:
                qs = qs.filter(effective_price__lte=float(max_price))
            except (TypeError, ValueError):
                pass

        # ---- Sort ----
        order_by = SORT_CHOICES.get(sort, '-created_at')
        qs = qs.order_by(order_by)

        return qs


class ProductDetailView(generics.RetrieveAPIView):
    """GET /api/v1/products/<slug>/"""
    serializer_class = ProductDetailSerializer
    lookup_field = 'slug'
    queryset = Product.objects.visible().select_related(
        'category', 'department'
    ).prefetch_related('variants', 'images', 'offers')


class DepartmentListView(generics.ListAPIView):
    """GET /api/v1/departments/"""
    serializer_class = DepartmentSerializer
    queryset = Department.objects.filter(is_active=True)
    pagination_class = None


class CategoryListView(generics.ListAPIView):
    """GET /api/v1/categories/ — filterable by ?department=<slug>"""
    serializer_class = CategorySerializer
    pagination_class = None

    def get_queryset(self):
        qs = Category.objects.select_related('department')
        department = self.request.query_params.get('department')
        if department:
            qs = qs.filter(department__slug=department)
        return qs
