from django.db import models
from rest_framework import generics, permissions, status
from django.contrib.auth import get_user_model
from .models import Tool, Payment, Sale, Customer, EquipmentType, Supplier, SaleItem
from .serializers import (
    UserSerializer, ToolSerializer, EquipmentTypeSerializer,
    PaymentSerializer, SaleSerializer, CustomerSerializer, SupplierSerializer, CustomerOwingSerializer
)
from .permissions import IsAdminOrStaff, IsOwnerOrAdmin
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.db.models import Sum, Count, Max
from django.core.mail import send_mail, BadHeaderError
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from datetime import timedelta
import secrets, uuid, traceback
import json


User = get_user_model()


# ----------------------------
# STAFF MANAGEMENT
# ----------------------------
class AddStaffView(generics.CreateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def post(self, request, *args, **kwargs):
        email = request.data.get("email")
        name = request.data.get("name")
        phone = request.data.get("phone")

        if not email:
            return Response({"detail": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email=email).exists():
            return Response({"detail": "User with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)

        password = secrets.token_urlsafe(10)

        user = User.objects.create_user(
            email=email,
            password=password,
            name=name or "",
            phone=phone or "",
            role="staff",
            is_active=True,
        )

        try:
            send_mail(
                subject="Your Staff Account Details",
                message=f"Hello {name or 'Staff'},\n\nYour account has been created.\n\nEmail: {email}\nPassword: {password}",
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "runocole@gmail.com"),
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception:
            traceback.print_exc()

        return Response(
            {
                "id": user.id,
                "email": email,
                "name": user.name,
                "phone": user.phone,
                "detail": "Staff created successfully",
            },
            status=status.HTTP_201_CREATED,
        )


class StaffListView(generics.ListAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def get_queryset(self):
        return User.objects.filter(role="staff")


# ----------------------------
# AUTHENTICATION
# ----------------------------
class EmailLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        email = request.data.get("email")
        password = request.data.get("password")

        if not email or not password:
            return Response({"detail": "Email and password are required."}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=email).first()
        if not user or not user.check_password(password):
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_400_BAD_REQUEST)

        if not user.is_active:
            return Response({"detail": "User account is disabled."}, status=status.HTTP_403_FORBIDDEN)

        # Auto-activate customer on first login
        if user.role == "customer":
            try:
                customer = Customer.objects.get(user=user)
                if not customer.is_activated:
                    customer.is_activated = True
                    customer.save()
            except Customer.DoesNotExist:
                pass

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


# ----------------------------
# CUSTOMERS
# ----------------------------
class AddCustomerView(generics.CreateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        email = request.data.get("email")
        name = request.data.get("name")
        phone = request.data.get("phone")
        state = request.data.get("state")

        if not email:
            return Response({"detail": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email=email).exists():
            return Response({"detail": "User with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)

        password = secrets.token_urlsafe(10)
        user = User.objects.create_user(
            email=email,
            password=password,
            name=name or "",
            phone=phone or "",
            role="customer",
            is_active=True,
        )

        Customer.objects.create(
            user=user, name=name, phone=phone, state=state, email=email
        )

        try:
            send_mail(
                subject="Your Customer Account Details",
                message=f"Hello {name or 'Customer'},\n\nAn account has been created for you.\nEmail: {email}\nPassword: {password}",
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "runocole@gmail.com"),
                recipient_list=[email],
                fail_silently=True,
            )
        except Exception as e:
            print("Failed to send email:", e)

        return Response(
            {"id": user.id, "email": email, "name": name, "phone": phone, "state": state},
            status=status.HTTP_201_CREATED,
        )


class CustomerListView(generics.ListAPIView):
    serializer_class = CustomerSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == "admin":
            return Customer.objects.all().order_by("-id")
        return Customer.objects.all().order_by("-id")
    
# ----------------------------
# CUSTOMER OWING/INSTALLMENT TRACKING
# ----------------------------
class CustomerOwingDataView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        try:
            # Get all customers
            customers = Customer.objects.all()
            
            # Calculate summary statistics
            total_selling_price = sum(customer.total_selling_price for customer in customers)
            total_amount_received = sum(customer.amount_paid for customer in customers)
            total_amount_left = sum(customer.amount_left for customer in customers)
            today = timezone.now().date()
            next_week = today + timedelta(days=7)
            
            upcoming_receivables = sum(
                customer.amount_left for customer in customers 
                if customer.date_next_installment and 
                customer.date_next_installment <= next_week and
                customer.status != 'fully-paid'
            )
            
            # Count overdue customers
            overdue_customers_count = customers.filter(
                status='overdue'
            ).count()
            
            # Prepare summary data
            summary = {
                "totalSellingPrice": float(total_selling_price),
                "totalAmountReceived": float(total_amount_received),
                "totalAmountLeft": float(total_amount_left),
                "upcomingReceivables": float(upcoming_receivables),
                "overdueCustomers": overdue_customers_count,
                "totalCustomers": customers.count()
            }
            
            # Serialize customer data
            customers_data = CustomerOwingSerializer(customers, many=True).data
            
            response_data = {
                "summary": summary,
                "customers": customers_data
            }
            
            return Response(response_data)
            
        except Exception as e:
            print(f"Error in CustomerOwingDataView: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(
                {"error": "Failed to fetch customer owing data"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
# ----------------------------
# TOOLS
# ----------------------------

class ToolListCreateView(generics.ListCreateAPIView):
    serializer_class = ToolSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Tool.objects.select_related("supplier").order_by("-date_added")

        # Filter by category and equipment type if provided
        category = self.request.query_params.get('category')
        equipment_type = self.request.query_params.get('equipment_type')
        
        if category:
            queryset = queryset.filter(category=category)
            
        if equipment_type:
            # For Receiver category, filter by description/box_type based on equipment_type
            if category == "Receiver":
                if equipment_type == "Base Only":
                    queryset = queryset.filter(description__icontains="base").exclude(description__icontains="rover")
                elif equipment_type == "Rover Only":
                    queryset = queryset.filter(description__icontains="rover").exclude(description__icontains="base")
                elif equipment_type == "Base & Rover Combo":
                    queryset = queryset.filter(description__icontains="base").filter(description__icontains="rover")
                elif equipment_type == "Accessories":
                    queryset = queryset.filter(description__icontains="accessory")

        if getattr(user, "role", None) == "customer":
            queryset = queryset.filter(stock__gt=0, is_enabled=True)

        return queryset

    def perform_create(self, serializer):
        user = self.request.user
        if getattr(user, "role", None) == "customer":
            raise permissions.PermissionDenied("Customers cannot add tools.")
        
        # Initialize available_serials with serials if not provided
        tool_data = serializer.validated_data
        if 'available_serials' not in tool_data or not tool_data['available_serials']:
            if 'serials' in tool_data and tool_data['serials']:
                tool_data['available_serials'] = tool_data['serials'].copy()
                
        serializer.save()

# NEW: Get tools grouped by name for frontend display
class ToolGroupedListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        user = self.request.user
        category = request.query_params.get('category')
        equipment_type = request.query_params.get('equipment_type')
        
        # Start with base queryset
        queryset = Tool.objects.filter(stock__gt=0, is_enabled=True)
        
        if category:
            queryset = queryset.filter(category=category)
            
        if equipment_type and category == "Receiver":
            # Apply equipment type filtering for Receiver category
            if equipment_type == "Base Only":
                queryset = queryset.filter(description__icontains="base").exclude(description__icontains="rover")
            elif equipment_type == "Rover Only":
                queryset = queryset.filter(description__icontains="rover").exclude(description__icontains="base")
            elif equipment_type == "Base & Rover Combo":
                queryset = queryset.filter(description__icontains="base").filter(description__icontains="rover")
            elif equipment_type == "Accessories":
                queryset = queryset.filter(description__icontains="accessory")
        
        # Group tools by name and calculate total stock
        from django.db.models import Sum, Count
        grouped_tools = queryset.values('name', 'category', 'cost').annotate(
            total_stock=Sum('stock'),
            tool_count=Count('id'),
            available_serials_count=Sum('stock')  # Assuming each stock item has one serial
        ).order_by('name')
        
        # Convert to list and add additional info
        result = []
        for tool_group in grouped_tools:
            # Get one sample tool for additional fields
            sample_tool = queryset.filter(name=tool_group['name']).first()
            if sample_tool:
                result.append({
                    'name': tool_group['name'],
                    'category': tool_group['category'],
                    'cost': tool_group['cost'],
                    'total_stock': tool_group['total_stock'],
                    'tool_count': tool_group['tool_count'],
                    'description': sample_tool.description,
                    'supplier_name': sample_tool.supplier.name if sample_tool.supplier else None,
                    'group_id': f"group_{tool_group['name'].replace(' ', '_').lower()}"
                })
        
        return Response(result)

# NEW: Assign random tool from group
class ToolAssignRandomFromGroupView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        tool_name = request.data.get('tool_name')
        category = request.data.get('category')
        
        if not tool_name:
            return Response({"error": "Tool name is required."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Find all available tools with this name and category that have enough serials
        available_tools = Tool.objects.filter(
            name=tool_name, 
            category=category,
            stock__gt=0,
            is_enabled=True
        )
        
        # === ADD YOUR COMBO LOGIC HERE ===
        # Check if this is a combo equipment type
        if "combo" in tool_name.lower():
            # For combo, we need to assign both base and rover equipment
            base_tools = Tool.objects.filter(
                name__icontains="base only",  # Adjust based on your actual tool names
                category=category,
                stock__gt=0,
                is_enabled=True
            )
            rover_tools = Tool.objects.filter(
                name__icontains="rover only",  # Adjust based on your actual tool names  
                category=category,
                stock__gt=0,
                is_enabled=True
            )
            
            if not base_tools or not rover_tools:
                return Response(
                    {"error": "Complete base and rover sets not available for combo."},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Get base serials (2 serials)
            base_tool = base_tools.first()
            base_serial_set = base_tool.get_random_serial_set()
            if not base_serial_set or len(base_serial_set) != 2:
                return Response(
                    {"error": "Failed to get complete base serial set."},
                    status=status.HTTP_404_NOT_FOUND
                )
            base_tool.decrease_stock()
            
            # Get rover serials (2 serials)  
            rover_tool = rover_tools.first()
            rover_serial_set = rover_tool.get_random_serial_set()
            if not rover_serial_set or len(rover_serial_set) != 2:
                return Response(
                    {"error": "Failed to get complete rover serial set."},
                    status=status.HTTP_404_NOT_FOUND
                )
            rover_tool.decrease_stock()
            
            # Combine all serials
            all_serials = base_serial_set + rover_serial_set
            
            return Response({
                "assigned_tool_id": f"combo_{base_tool.id}_{rover_tool.id}",
                "tool_name": tool_name,
                "serial_set": all_serials,  # This should be 4 serials total
                "serial_count": len(all_serials),
                "set_type": "Base & Rover Combo",
                "cost": str(float(base_tool.cost) + float(rover_tool.cost)),
                "description": "Base & Rover Combo Set",
                "remaining_stock": min(base_tool.stock, rover_tool.stock),
                "import_invoice": base_tool.invoice_number  # Use base tool invoice
            })
        
        # === NORMAL LOGIC FOR NON-COMBO EQUIPMENT ===
        # Filter tools that have enough serials for a complete set
        tools_with_enough_serials = []
        for tool in available_tools:
            set_count = tool.get_serial_set_count()
            if tool.available_serials and len(tool.available_serials) >= set_count:
                tools_with_enough_serials.append(tool)
        
        if not tools_with_enough_serials:
            return Response(
                {"error": f"No complete {tool_name} sets available in stock."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Select a random tool from available ones
        import random
        selected_tool = random.choice(tools_with_enough_serials)
        
        # Get a complete serial SET (2 or 4 serials depending on equipment type)
        serial_set = selected_tool.get_random_serial_set()
        
        if not serial_set:
            return Response(
                {"error": "Failed to get complete serial set from selected tool."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Update the stock count
        selected_tool.decrease_stock()
        
        return Response({
            "assigned_tool_id": selected_tool.id,
            "tool_name": selected_tool.name,
            "serial_set": serial_set,  # This is now an ARRAY of serials
            "serial_count": len(serial_set),
            "set_type": selected_tool.description,
            "cost": str(selected_tool.cost),
            "description": selected_tool.description,
            "remaining_stock": selected_tool.stock,
            "import_invoice": selected_tool.invoice_number
        })
    
# NEW: Get random serial number for a tool
class ToolGetRandomSerialView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, pk):
        try:
            tool = get_object_or_404(Tool, pk=pk)
            
            if not tool.available_serials:
                return Response(
                    {"error": "No available serial numbers for this tool."},
                    status=status.HTTP_404_NOT_FOUND
                )
                
            random_serial = tool.get_random_serial()
            
            if not random_serial:
                return Response(
                    {"error": "Failed to get random serial number."},
                    status=status.HTTP_404_NOT_FOUND
                )
                
            return Response({
                "serial_number": random_serial,
                "tool_name": tool.name,
                "remaining_serials": len(tool.available_serials)
            })
            
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
class ToolDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Tool.objects.all()
    serializer_class = ToolSerializer
    permission_classes = [permissions.IsAuthenticated]


class ToolSoldSerialsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, pk):
        tool = get_object_or_404(Tool, pk=pk)
        
        sold_serials = []
        for serial_info in tool.sold_serials or []:
            if isinstance(serial_info, dict):
                sold_serials.append({
                    'serial': serial_info.get('serial', 'Unknown'),
                    'sale_id': serial_info.get('sale_id'),
                    'customer_name': serial_info.get('customer_name', 'Unknown'),
                    'date_sold': serial_info.get('date_sold'),
                    'invoice_number': serial_info.get('invoice_number'),
                    'import_invoice': serial_info.get('import_invoice')  # NEW: Add import_invoice
                })
            else:
                # Handle case where serial_info is just a string
                sold_serials.append({
                    'serial': serial_info,
                    'sale_id': None,
                    'customer_name': 'Unknown',
                    'date_sold': None,
                    'invoice_number': None,
                    'import_invoice': None  # NEW: Add import_invoice
                })
                
        return Response(sold_serials)

# ----------------------------
# EQUIPMENT TYPE
# ----------------------------

class EquipmentTypeListView(generics.ListCreateAPIView):
    serializer_class = EquipmentTypeSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        queryset = EquipmentType.objects.all().order_by("category", "name")
        
        # Filter by invoice_number if provided
        invoice_number = self.request.query_params.get('invoice_number')
        if invoice_number:
            queryset = queryset.filter(invoice_number=invoice_number)
        
        # Filter by category if provided
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)
            
        return queryset

class EquipmentTypeDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = EquipmentType.objects.all()
    serializer_class = EquipmentTypeSerializer
    permission_classes = [permissions.AllowAny]

# NEW VIEW: Get equipment grouped by invoice
@api_view(['GET'])
def equipment_by_invoice(request):
    """
    Get equipment types grouped by invoice number with counts and totals
    """
    from django.db.models import F, FloatField
    from django.db.models.functions import Cast
    
    invoices = EquipmentType.objects.exclude(invoice_number__isnull=True)\
        .exclude(invoice_number__exact='')\
        .values('invoice_number')\
        .annotate(
            equipment_count=Count('id'),
            total_value=Sum('default_cost'),
            last_updated=Max('created_at')
        )\
        .order_by('-last_updated')
    
    return Response(list(invoices))

#-------------------
# SUPPLIERS
#--------------------
class SupplierListView(generics.ListCreateAPIView):
    queryset = Supplier.objects.all().order_by("name")
    serializer_class = SupplierSerializer
    permission_classes = [permissions.AllowAny]

class SupplierDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [permissions.AllowAny]

# ----------------------------
# SALES
# ----------------------------
class SaleListCreateView(generics.ListCreateAPIView):
    serializer_class = SaleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == "staff":
            return Sale.objects.filter(staff=user).order_by("-date_sold")
        elif user.role == "admin":
            return Sale.objects.all().order_by("-date_sold")
        return Sale.objects.none()

    def perform_create(self, serializer):
        # Get import_invoice from request data
        import_invoice = self.request.data.get('import_invoice')
        
        # Save the sale with staff and import_invoice
        sale = serializer.save(
            staff=self.request.user,
            import_invoice=import_invoice
        )
        
        # FIXED: Also update sale items with serial_set data
        if sale.items.exists():
            items_data = self.request.data.get('items', [])
            for i, item in enumerate(sale.items.all()):
                if i < len(items_data):
                    item_data = items_data[i]
                    # Save serial_set to the item
                    serial_set = item_data.get('serial_set')
                    if serial_set:
                        # Convert serial_set array to serial_number field
                        if isinstance(serial_set, list) and len(serial_set) > 0:
                            if len(serial_set) == 1:
                                item.serial_number = serial_set[0]
                            else:
                                item.serial_number = json.dumps(serial_set)
                        item.save()
        
        # Also update sale items with import_invoice if provided
        if import_invoice and sale.items.exists():
            sale.items.all().update(import_invoice=import_invoice)


class SaleDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = SaleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == "staff":
            return Sale.objects.filter(staff=user)
        elif user.role == "admin":
            return Sale.objects.all()
        return Sale.objects.none()

    def perform_update(self, serializer):
        user = self.request.user
        instance = self.get_object()
        if user.role == "staff" and instance.staff != user:
            raise PermissionDenied("You can only edit your own sales.")
        return super().perform_update(serializer)

# ----------------------------
# EMAIL API
# ----------------------------
@api_view(["POST"])
@permission_classes([AllowAny])  
def send_sale_email(request):
    try:
        data = request.data
        send_mail(
            subject=data.get("subject", "Your Payment Link"),
            message=data.get("message", "Hello, your payment link will be available soon."),
            from_email="runocole@gmail.com",  
            recipient_list=[data.get("to_email")],
            fail_silently=False,
        )
        return Response({"message": "Email sent successfully!"})
    except Exception as e:
        return Response({"error": str(e)}, status=500)
# ----------------------------
# DASHBOARD SUMMARY
# ----------------------------
class DashboardSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        total_sales = Sale.objects.count()
        total_revenue = Sale.objects.aggregate(total=Sum("total_cost"))["total"] or 0
        tools_count = Tool.objects.filter(stock__gt=0).count()
        staff_count = User.objects.filter(role="staff").count()
        active_customers = Customer.objects.filter(is_activated=True).count()

        today = timezone.now()
        month_start = today.replace(day=1)
        mtd_revenue = (
            Sale.objects.filter(date_sold__gte=month_start)
            .aggregate(total=Sum("total_cost"))
            .get("total")
            or 0
        )

        inventory_breakdown = []
        receiver_tools_breakdown = (
            Tool.objects
            .filter(category="Receiver", stock__gt=0)  
            .values("name")
            .annotate(count=Count("id"))
            .order_by("name")
        )
        
        for item in receiver_tools_breakdown:
            inventory_breakdown.append({
                "receiver_type": item["name"],
                "count": item["count"]
            })

        if not inventory_breakdown:
            inventory_breakdown.append({
                "receiver_type": "No receiver tools",
                "count": 0
            })

        # ✅ FIXED: Low stock should only show items that actually have stock
        low_stock_items = list(
            Tool.objects.filter(stock__lte=5, stock__gt=0)  # Only items with stock
            .values("id", "name", "code", "category", "stock")[:5]
        )

        # Top selling tools
        top_selling_tools = (
            SaleItem.objects.values("tool__name")
            .annotate(total_sold=Count("id"))
            .order_by("-total_sold")[:5]
        )

        # Recent sales
        recent_sales = Sale.objects.prefetch_related('items').order_by('-date_sold')[:10]
        recent_sales_data = []
        for sale in recent_sales:
            first_item = sale.items.first()
            tool_name = first_item.equipment if first_item else "No equipment"
            
            recent_sales_data.append({
                'invoice_number': sale.invoice_number,
                'customer_name': sale.name,
                'tool_name': tool_name,
                'cost_sold': sale.total_cost,
                'payment_status': sale.payment_status,
                'date_sold': sale.date_sold,
                'import_invoice': sale.import_invoice  # NEW: Add import_invoice to recent sales
            })

        # Expiring receivers - only show items with stock
        thirty_days_from_now = timezone.now().date() + timedelta(days=30)
        expiring_receivers = (
            Tool.objects
            .filter(
                category="Receiver",
                expiry_date__isnull=False,
                expiry_date__gt=timezone.now().date(),
                expiry_date__lte=thirty_days_from_now,
                stock__gt=0  # Only show items that are in stock
            )
            .values("name", "code", "expiry_date")
            .order_by("expiry_date")[:10]
        )
        
        expiring_receivers_data = []
        for receiver in expiring_receivers:
            expiring_receivers_data.append({
                "name": receiver["name"],
                "serialNumber": receiver["code"],
                "expirationDate": receiver["expiry_date"].isoformat() if receiver["expiry_date"] else None
            })

        return Response(
            {
                "totalTools": tools_count,
                "totalStaff": staff_count,
                "activeCustomers": active_customers,
                "mtdRevenue": mtd_revenue,
                "inventoryBreakdown": inventory_breakdown,
                "lowStockItems": low_stock_items,
                "topSellingTools": list(top_selling_tools),
                "recentSales": recent_sales_data,
                "expiringReceivers": expiring_receivers_data,
            }
        )

# ----------------------------
# PAYMENTS
# ----------------------------
class PaymentListCreateView(generics.ListCreateAPIView):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]


class PaymentDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer
    permission_classes = [IsOwnerOrAdmin]