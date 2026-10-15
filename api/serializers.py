from rest_framework import serializers

from store.models import Category, Department, Product


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ('id', 'name', 'slug', 'theme', 'icon_kind')


class CategorySerializer(serializers.ModelSerializer):
    department_slug = serializers.CharField(source='department.slug', read_only=True)

    class Meta:
        model = Category
        fields = ('id', 'name', 'slug', 'department', 'department_slug')


class ProductListSerializer(serializers.ModelSerializer):
    price_range = serializers.SerializerMethodField()
    in_stock = serializers.BooleanField(read_only=True)
    image = serializers.SerializerMethodField()
    vendor_name = serializers.CharField(
        source='vendor.business_name', read_only=True, default=None,
    )

    class Meta:
        model = Product
        fields = (
            'id', 'name', 'slug', 'image', 'price', 'price_range',
            'compare_at_price', 'in_stock', 'availability',
            'is_rentable', 'daily_rate', 'rental_deposit',
            'vendor_name', 'category', 'department',
        )

    def get_price_range(self, obj):
        lo, hi = obj.price_range
        return [str(lo), str(hi)]

    def get_image(self, obj):
        img = obj.primary_image
        return img.url if img else None


class ProductDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = '__all__'
