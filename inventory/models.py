from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.conf import settings
from django.dispatch import receiver
from django.db.models.signals import post_save
import uuid, random, string
from datetime import date
from django.contrib.auth import get_user_model
from django.utils import timezone

# ----------------------------
#  USER
# ----------------------------
class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "admin")
        if not password:
            raise ValueError("Superuser must have a password")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    ROLE_CHOICES = (
        ("admin", "Admin"),
        ("staff", "Staff"),
        ("customer", "Customer"),
    )

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="staff")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


# ----------------------------
#  CUSTOMERS
# ----------------------------
class Customer(models.Model):
    STATUS_CHOICES = [
        ('on-track', 'On Track'),
        ('due-soon', 'Due Soon'),
        ('overdue', 'Overdue'),
        ('fully-paid', 'Fully Paid')
    ]
    
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="customer",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    is_activated = models.BooleanField(default=False)
    
    # Installment tracking fields
    total_selling_price = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        verbose_name="Total Selling Price"
    )
    amount_paid = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        verbose_name="Amount Paid"
    )
    amount_left = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        verbose_name="Amount Left"
    )
    date_last_paid = models.DateField(
        null=True, 
        blank=True,
        verbose_name="Date Last Paid"
    )
    date_next_installment = models.DateField(
        null=True, 
        blank=True,
        verbose_name="Next Installment Date"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='on-track',
        verbose_name="Payment Status"
    )
    progress = models.IntegerField(
        default=0,
        verbose_name="Payment Progress (%)",
        help_text="Percentage of total amount paid"
    )

    def __str__(self):
        return self.name or "Unnamed Customer"

    def save(self, *args, **kwargs):
        # Auto-calculate amount_left and progress before saving
        if self.total_selling_price > 0:
            self.amount_left = self.total_selling_price - self.amount_paid
            self.progress = int((self.amount_paid / self.total_selling_price) * 100)
            
            # Auto-update status based on amounts and dates
            self.update_status()
        else:
            self.amount_left = 0
            self.progress = 0
            
        super().save(*args, **kwargs)

    def update_status(self):
        """Update customer status based on payment progress and dates"""
        from django.utils import timezone
        from datetime import timedelta
        
        if self.amount_left <= 0:
            self.status = 'fully-paid'
            return
        
        today = timezone.now().date()
        
        if not self.date_next_installment:
            self.status = 'on-track'
            return
            
        # Check if overdue (past due date)
        if self.date_next_installment < today:
            self.status = 'overdue'
        # Check if due soon (within next 7 days)
        elif self.date_next_installment <= today + timedelta(days=7):
            self.status = 'due-soon'
        else:
            self.status = 'on-track'

    def make_payment(self, amount, payment_date=None):
        """Helper method to record a payment"""
        from django.utils import timezone
        
        self.amount_paid += amount
        
        if payment_date:
            self.date_last_paid = payment_date
        else:
            self.date_last_paid = timezone.now().date()
            
        self.save()

    def set_next_installment_date(self, date):
        """Set the next installment date"""
        self.date_next_installment = date
        self.save()

    @property
    def is_overdue(self):
        """Check if customer is overdue on payments"""
        from django.utils import timezone
        if self.date_next_installment:
            return self.date_next_installment < timezone.now().date()
        return False

    @property
    def is_due_soon(self):
        """Check if payment is due within 7 days"""
        from django.utils import timezone
        from datetime import timedelta
        
        if self.date_next_installment:
            next_week = timezone.now().date() + timedelta(days=7)
            return (timezone.now().date() < self.date_next_installment <= next_week)
        return False


@receiver(post_save, sender=Customer)
def create_user_for_customer(sender, instance, created, **kwargs):
    if created and not instance.user:
        user = User.objects.create_user(
            email=instance.email or f"{instance.phone}@example.com",
            password="defaultpass123",
            role="customer",
            is_active=False,
        )
        instance.user = user
        instance.save()



# ----------------------------
#  TOOLS MODEL
# ----------------------------


class Tool(models.Model):
    CATEGORY_CHOICES = (
        ("Receiver", "Receiver"),
        ("Accessory", "Accessory"),
        ("Total Station", "Total Station"),
        ("Level", "Level"),
        ("Drones", "Drones"),
        ("EcoSounder", "EcoSounder"),
        ("Laser Scanner", "Laser Scanner"),
        ("Other", "Other"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    description = models.TextField(blank=True)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=1)

    supplier = models.ForeignKey(
        "Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tools",
    )

    equipment_type = models.ForeignKey(
        "EquipmentType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tools",
        verbose_name="Equipment Type"
    )

    is_enabled = models.BooleanField(default=True)
    invoice_number = models.CharField(max_length=50, blank=True, null=True)
    date_added = models.DateTimeField(auto_now_add=True)
    expiry_date = models.DateField(null=True, blank=True, verbose_name="Expiry Date")

    # JSON Storage for Serials
    serials = models.JSONField(default=list, blank=True)
    available_serials = models.JSONField(default=list, blank=True)
    pending_serials = models.JSONField(default=list, blank=True)  # Reserved during selection
    sold_serials = models.JSONField(default=list, blank=True)     # Finalized sales

    def __str__(self):
        return f"{self.name} ({self.code})"

    # --- STOCK MANAGEMENT ---

    def decrease_stock(self):
        """Reduces stock count manually if needed."""
        if self.stock > 0:
            self.stock -= 1
            self.save(update_fields=["stock"])

    def increase_stock(self):
        """Increases stock count manually if needed."""
        self.stock += 1
        self.save(update_fields=["stock"])

    # --- SERIAL ASSIGNMENT LOGIC ---

    def get_serial_set_count(self):
        """Calculates how many serials are needed based on description."""
        if not self.description:
            return 1
        
        desc = self.description.lower()
        if "base only" in desc or "rover only" in desc:
            return 2  # receiver + datalogger
        elif "combo" in desc or "base and rover" in desc:
            return 4  # 2 receivers + 2 dataloggers
        return 1

    def get_random_serial_set(self):
        """
        Logic for the 'Assign' button. 
        Moves serials to PENDING and decreases stock immediately.
        """
        set_count = self.get_serial_set_count()
        
        if not self.available_serials or len(self.available_serials) < set_count:
            return None
            
        # 1. Grab the next available set
        serial_set = self.available_serials[:set_count]
        
        # 2. Remove from available
        self.available_serials = self.available_serials[set_count:]
        
        # 3. Add to pending (so fast loops don't grab them again)
        pending_entry = {
            'serial_set': serial_set,
            'reserved_at': timezone.now().isoformat(),
            'import_invoice': self.invoice_number
        }
        self.pending_serials.append(pending_entry)
        
        # 4. Auto-decrease stock
        if self.stock > 0:
            self.stock -= 1

        self.save(update_fields=["available_serials", "pending_serials", "stock"])
        return serial_set

    def restore_serials(self, serial_set):
        """
        Call this if a user removes an item from the sale table 
        WITHOUT finishing the sale. Puts serials back and restores stock.
        """
        # Remove from pending list
        self.pending_serials = [p for p in self.pending_serials if p.get('serial_set') != serial_set]
        
        # Put back in available
        self.available_serials.extend(serial_set)
        
        # Restore stock count
        self.stock += 1
        
        self.save(update_fields=["available_serials", "pending_serials", "stock"])

    def finalize_sale_serials(self, serial_set, sale_id, customer_name):
        """
        Call this when 'Save Sale' is clicked. 
        Moves serials from PENDING to SOLD permanently.
        """
        # 1. Remove from pending
        self.pending_serials = [p for p in self.pending_serials if p.get('serial_set') != serial_set]

        # 2. Add to sold with details
        sold_info = {
            'serial_set': serial_set,
            'sale_id': str(sale_id),
            'customer': customer_name,
            'date_sold': timezone.now().date().isoformat(),
            'import_invoice': self.invoice_number
        }
        self.sold_serials.append(sold_info)
        
        self.save(update_fields=["pending_serials", "sold_serials"])

    # --- PROPERTIES ---

    @property
    def display_equipment_type(self):
        return self.equipment_type.name if self.equipment_type else "N/A"

    @property
    def is_expired(self):
        return self.expiry_date < timezone.now().date() if self.expiry_date else False

    @property
    def expires_soon(self):
        if self.expiry_date:
            from datetime import timedelta
            thirty_days = timezone.now().date() + timedelta(days=30)
            return timezone.now().date() < self.expiry_date <= thirty_days
        return False

# ----------------------------
#  EQUIPMENT TYPES
# ----------------------------        
class EquipmentType(models.Model):  
    CATEGORY_CHOICES = [
        ("Receiver", "Receiver"),
        ("Accessory", "Accessory"), 
        ("Total Station", "Total Station"),
        ("Level", "Level"),
        ("Drones", "Drones"),
        ("EcoSounder", "EcoSounder"),
        ("Laser Scanner", "Laser Scanner"),
        ("Other", "Other"),
    ]
    
    name = models.CharField(max_length=100)
    default_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    naira_cost = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="Receiver")
    description = models.TextField(blank=True, null=True)
    invoice_number = models.CharField(max_length=100, blank=True, null=True)  # NEW FIELD
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.category} - {self.name}"

    class Meta:
        indexes = [
            models.Index(fields=['invoice_number']),
            models.Index(fields=['category']),
        ]
    
#----------------------------
# SUPPLIERS 
#----------------------------

class Supplier(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
    
#----------------------------
# SALES 
#----------------------------
class Sale(models.Model):
    PAYMENT_STATUS_CHOICES = (
        ("pending", "Pending"),
        ('ongoing', 'Ongoing'),
        ("completed", "Completed"),
        ("installment", "Installment"),
        ("failed", "Failed"),
    )

    # 🔹 Who made the sale (staff)
    staff = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="sales_made",
        limit_choices_to={"role": "staff"},
        null=True,
        blank=True,
    )

    # 🔹 Customer information (stored directly in Sale)
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20)
    state = models.CharField(max_length=100)
    total_cost = models.DecimalField(max_digits=10, decimal_places=2)
    date_sold = models.DateField(default=timezone.now)
    invoice_number = models.CharField(max_length=100, unique=True, blank=True)
    payment_plan = models.CharField(max_length=100, blank=True, null=True)
    initial_deposit = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        blank=True, 
        null=True,
        verbose_name="Initial Deposit Amount"
    )
    payment_months = models.IntegerField(
        blank=True, 
        null=True,
        verbose_name="Number of Payment Months"
    )
    expiry_date = models.DateField(blank=True, null=True)
    payment_status = models.CharField(
        max_length=20, choices=PAYMENT_STATUS_CHOICES, default="pending"
    )
    
    # NEW: Add import_invoice field
    import_invoice = models.CharField(
        max_length=100, 
        blank=True, 
        null=True, 
        verbose_name="Import Invoice Number"
    )

    def __str__(self):
        return f"{self.name} - {self.invoice_number}"

    def save(self, *args, **kwargs):
        """Auto-generate invoice number on creation."""
        if not self.invoice_number:
            self.invoice_number = f"INV-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
        
        # Ensure installment fields are cleared when payment plan is "No"
        if self.payment_plan == "No":
            self.initial_deposit = None
            self.payment_months = None
            
        super().save(*args, **kwargs)

class SaleItem(models.Model):
    """Individual items within a sale"""
    sale = models.ForeignKey(Sale, related_name="items", on_delete=models.CASCADE)
    tool = models.ForeignKey(Tool, on_delete=models.CASCADE)
    equipment = models.CharField(max_length=255)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.CharField(max_length=100, blank=True, null=True)
    serial_number = models.CharField(max_length=100, blank=True, null=True)
    assigned_tool_id = models.CharField(max_length=100, blank=True, null=True)
    
    # NEW: Add import_invoice field to SaleItem
    import_invoice = models.CharField(
        max_length=100, 
        blank=True, 
        null=True, 
        verbose_name="Import Invoice Number"
    )

    equipment_type = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Equipment Type",
        help_text="Base Only, Rover Only, Base & Rover Combo, etc."
    )

    external_radio_serial = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="External Radio Serial"
    )

    def __str__(self):
        return f"{self.equipment} - ₦{self.cost}"

    def save(self, *args, **kwargs):
        """Deduct stock on first save only"""
        if not self.pk and self.tool.stock > 0:
            self.tool.decrease_stock()
            
            # If serial number is provided, mark it as sold in the tool
            if self.serial_number and self.sale_id:
                self.tool.add_sold_serial_info(
                    serial=self.serial_number,
                    sale_id=self.sale_id,
                    customer_name=self.sale.name,
                    invoice_number=self.sale.invoice_number
                )
                
        super().save(*args, **kwargs)

# ----------------------------
#  PAYMENTS
# ----------------------------
class Payment(models.Model):
    PAYMENT_METHODS = [
        ("cash", "Cash"),
        ("transfer", "Bank Transfer"),
        ("paystack", "Paystack"),
    ]
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    customer = models.ForeignKey(User, on_delete=models.CASCADE)
    sale = models.ForeignKey(Sale, on_delete=models.SET_NULL, null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default="paystack")
    payment_reference = models.CharField(max_length=100, blank=True, null=True)
    payment_date = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    def __str__(self):
        return f"Payment {self.id} - {self.customer.email}"

# ----------------------------
#  ACTIVATION CODES
# ----------------------------

class CodeBatch(models.Model):
    """Batch of codes received from China"""
    batch_number = models.CharField(max_length=100, unique=True)
    received_date = models.DateField(default=timezone.now)
    supplier = models.CharField(max_length=200, default="China Supplier")
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.batch_number} ({self.received_date})"
    
class BatchSerial(models.Model):
    """
    Stores individual receiver serials imported from a CSV into a CodeBatch.

    status values (match your CSV exactly):
        'not sold'  → In Stock tab
        'active'    → Sold tab (assigned to a customer)
    """
    batch          = models.ForeignKey(CodeBatch, on_delete=models.CASCADE, related_name='serials')
    serial_number  = models.CharField(max_length=100)
    status         = models.CharField(max_length=50, default='not sold')
    payment_status = models.CharField(max_length=50, default='not_applicable')
    customer_email = models.EmailField(blank=True, null=True)
    customer_name  = models.CharField(max_length=255, blank=True, null=True)
    assigned_date  = models.DateField(blank=True, null=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('batch', 'serial_number')
        ordering = ['serial_number']

    def __str__(self):
        return f"{self.serial_number} ({self.batch.batch_number}) — {self.status}"


class ActivationCode(models.Model):
    
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('assigned', 'Assigned'),
        ('activated', 'Activated'),
        ('expired', 'Expired'),
    ]
    
    # The actual code
    code = models.CharField(max_length=100, unique=True)
    
    # Code properties
    batch = models.ForeignKey(CodeBatch, on_delete=models.SET_NULL, null=True, related_name='codes')
    
    # Assignment
    receiver_serial = models.CharField(max_length=100, blank=True, null=True)  # Serial number of the receiver
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name='codes')
    sale = models.ForeignKey(Sale, on_delete=models.SET_NULL, null=True, blank=True, related_name='codes')
    
    # Status tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')
    is_emergency = models.BooleanField(default=False)  # Emergency vs regular code
    
    # Dates
    assigned_date = models.DateTimeField(null=True, blank=True)
    activated_date = models.DateTimeField(null=True, blank=True)
    expiry_date = models.DateTimeField(null=True, blank=True)  # Can still set this manually
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    #QR code image field 
    qr_code_image = models.TextField(null=True, blank=True, help_text="Base64 encoded QR code image")
    
    def __str__(self):
        return f"{self.code} - {self.get_status_display()}"
    
    @property
    def is_expired(self):
        """Check if code is expired"""
        if self.expiry_date:
            from django.utils import timezone
            return timezone.now() > self.expiry_date
        return False
    
    @property
    def is_active(self):
        """Check if code is currently active (assigned and not expired)"""
        if self.status == 'assigned' and not self.is_expired:
            return True
        return False


class CodeAssignmentLog(models.Model):
    """Log of code assignments for audit trail"""
    code = models.ForeignKey(ActivationCode, on_delete=models.CASCADE, related_name='assignment_logs')
    receiver_serial = models.CharField(max_length=100)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True)
    sale = models.ForeignKey(Sale, on_delete=models.SET_NULL, null=True)
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    assigned_date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)
    
    def __str__(self):
        return f"Code {self.code.code} → {self.receiver_serial}"