/* Frontend JS: talks to FastAPI backend via fetch() */
/* Frontend JS: talks to FastAPI backend via fetch() */
let sessionId = null;
let categories = [];
let selectedCategory = null;
let selectedItemId = null; // ✅ track clicked item

function el(id){ return document.getElementById(id); }
function show(msg){ el("messages").innerText = msg; }

async function fetchCategories(){
  show("Loading categories...");
  try{
    let res = await fetch('/categories');
    if(!res.ok) throw await res.text();
    categories = await res.json();
    renderCategoryList();
    show("");
  }catch(e){
    show("Failed to load categories: " + e);
  }
}

function renderCategoryList(){
  const list = el("category-list");
  list.innerHTML = "";
  if(!Array.isArray(categories) || categories.length===0){
    list.innerText = "No categories available.";
    return;
  }
  categories.forEach((c, idx) => {
    const name = c.categoryName || c.name || c;
    const div = document.createElement("div");
    div.innerText = `${idx+1}. ${name}`;
    list.appendChild(div);
  });
}

async function openCategoryByNumber(){
  const n = parseInt(el("cat-number").value || "0");
  if(!n || n < 1 || n > categories.length){ alert("Invalid category number"); return; }
  selectedCategory = categories[n-1].categoryName || categories[n-1].name || categories[n-1];
  await loadItems(selectedCategory);
  el("back-to-cats").style.display = "inline-block";
}

async function loadItems(category){
  el("items-area").innerHTML = `<h4>📦 Items in ${category}</h4>`;
  try{
    let res = await fetch(`/items/${encodeURIComponent(category)}`);
    if(!res.ok){
      const text = await res.text();
      el("items-area").innerHTML += `<div class="small-muted">Failed to load items: ${text}</div>`;
      return;
    }
    const items = await res.json();
    if(!Array.isArray(items) || items.length===0){
      el("items-area").innerHTML += `<div class="small-muted">No items found in ${category}</div>`;
      return;
    }

    // ✅ sari items ek sath render karo
    items.forEach(it => {
      const row = document.createElement("div");
      row.className = "item-row";

      // unique id
      const id = it._id || it.id || it.itemName;

      if(selectedItemId === id){
        // agar ye item select hua hai → qty + add button dikhao
        row.innerHTML = `
          <h4>${it.itemName} - ${it.price ? it.price + " Rs" : "N/A"}</h4>
          <input class="qty-input" type="number" min="1" max="100" value="1" id="qty_${id}">
          <button class="action-btn" onclick='addToCart(${JSON.stringify(it)})'>🛒 Add to Cart</button>
        `;
      }else{
        // normally sirf naam + price
        row.innerHTML = `
          <h4 style="cursor:pointer;" onclick="selectItem('${id}')">${it.itemName} - ${it.price ? it.price + " Rs" : "N/A"}</h4>
        `;
      }

      el("items-area").appendChild(row);
    });

  }catch(e){
    el("items-area").innerHTML += `<div class="small-muted">Error: ${e}</div>`;
  }
}

// ✅ jab user kisi item pe click kare
function selectItem(id){
  selectedItemId = id;
  loadItems(selectedCategory); // dubara render karo taki sirf is item pe qty + add aaye
}

async function addToCart(it){
  if(!sessionId){ alert("Session not started"); return; }
  const id = it._id || it.id || it.itemName;
  const qel = el(`qty_${id}`);
  const qty = parseInt(qel ? qel.value : 1);
  const payload = { session_id: sessionId, itemName: it.itemName, price: it.price || 0, qty: qty };
  try{
    const res = await fetch("/cart/add", {
      method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(payload)
    });
    if(!res.ok) { const t = await res.text(); throw t; }
    const data = await res.json();
    renderCart(data.cart);
    show(`✅ ${it.itemName} added to cart!`);
    selectedItemId = null; // reset after adding
    loadItems(selectedCategory);
  }catch(e){
    show("Failed to add to cart: " + e);
  }
}

function renderCart(cart){
  const lines = el("cart-lines");
  lines.innerHTML = "";
  let total = 0;
  let totalItems = 0;
  cart.forEach(it => {
    const div = document.createElement("div");
    div.className = "cart-line";
    div.innerText = `${it.name} × ${it.qty} = ${it.subtotal} Rs`;
    lines.appendChild(div);
    total += it.subtotal;
    totalItems += it.qty;
  });
  el("cart-total").innerText = `💰 Total: ${total} Rs`;

  if(cart.length > 0){
    el("cart-area").style.display = "block";
  }

  const user = el("cart-user");
  if(sessionId){
    fetch(`/cart/view?session_id=${sessionId}`).then(r=>r.json()).then(data=>{
      const uname = data.user.name || "User";
      user.innerText = `👤 ${uname} | 📦 ${totalItems} items\n🏠 ${data.user.address || ""}`;
    }).catch(()=>{});
  }
}

async function startSession(){
  const country = el("country_code").value || "+92";
  const mobile = el("mobile").value.trim();
  const name = el("name").value.trim();      // ✅ fixed
  const address = el("address").value.trim(); // ✅ fixed

  if(!/^\d+$/.test(mobile)){ el("mobile-error").innerText = "Mobile must contain only digits."; return; }
  if(!mobile.startsWith("3")){ el("mobile-error").innerText = "Mobile must start with 3."; return; }
  if(mobile.length !== 10){ el("mobile-error").innerText = "Mobile must be 10 digits."; return; }
  el("mobile-error").innerText = "";

  const payload = { name, mobile, address, country_code: country };
  try{
    const res = await fetch("/session/create", {
      method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(payload)
    });
    if(!res.ok){ const t = await res.text(); throw t; }
    const data = await res.json();
    sessionId = data.session_id;

    el("form-area").style.display = "none";
    el("shopping-area").style.display = "block";
    el("cart-area").style.display = "block";

    el("user-info").innerText = `👤 ${data.user.name}  •  🏠 ${data.user.address}`;
    fetchCategories();
    renderCart([]);
  }catch(e){
    alert("Failed to create session: " + e);
  }
}

async function viewCartNow(){
  if(!sessionId) return;
  try{
    const r = await fetch(`/cart/view?session_id=${sessionId}`);
    if(!r.ok) throw await r.text();
    const d = await r.json();
    renderCart(d.cart || []);
  }catch(e){
    show("Could not fetch cart: " + e);
  }
}

async function openCheckout(){ el("checkout-area").style.display = "block"; }

async function confirmOrder(){
  if(!sessionId){ alert("No session"); return; }
  const pm = document.querySelector('input[name="payment"]:checked').value;
  try{
    const res = await fetch("/checkout", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ session_id: sessionId, paymentMethod: pm })
    });
    if(!res.ok){ const t = await res.text(); throw t; }
    const r = await res.json();
    show("✅ Order placed successfully.");
    renderCart([]);
    el("checkout-area").style.display = "none";
  }catch(e){
    show("Checkout failed: " + e);
  }
}

document.addEventListener("DOMContentLoaded", function(){
  el("start-btn").onclick = startSession;
  el("open-cat").onclick = openCategoryByNumber;
  el("back-to-cats").onclick = function(){
    selectedCategory = null;
    selectedItemId = null;
    el("items-area").innerHTML = "";
    el("back-to-cats").style.display = "none";
  };
  el("checkout-btn").onclick = openCheckout;
  el("confirm-order").onclick = confirmOrder;
});
