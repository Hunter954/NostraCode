# ADD THESE ROUTES
@app.route('/admin/clients/update/<int:id>', methods=['POST'])
def update_client(id):
    c = Client.query.get(id)
    c.name = request.form.get('name')
    c.email = request.form.get('email')
    db.session.commit()
    return redirect('/admin/clients')

@app.route('/admin/clients/delete/<int:id>')
def delete_client(id):
    c = Client.query.get(id)
    db.session.delete(c)
    db.session.commit()
    return redirect('/admin/clients')
