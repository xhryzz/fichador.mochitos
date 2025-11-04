from flask import Flask, render_template, redirect, url_for, flash, request, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import db, User, Exercise, ExerciseEntry, Workout, Routine, WorkoutTemplate, Meal, Diet
from forms import (RegistrationForm, LoginForm, ExerciseForm, ExerciseEntryForm, 
                   WorkoutForm, RoutineForm, MealForm, DietForm, FriendCodeForm)
from datetime import datetime
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = 'tu-clave-secreta-super-segura-cambiala'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///gym_tracker.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Crear las tablas
with app.app_context():
    db.create_all()

# ============= RUTAS DE AUTENTICACIÓN =============

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, email=form.email.data)
        user.set_password(form.password.data)
        user.generate_friend_code()
        db.session.add(user)
        db.session.commit()
        flash('¡Registro exitoso! Ya puedes iniciar sesión.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', form=form)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            flash('¡Bienvenido de nuevo!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Usuario o contraseña incorrectos', 'error')
    return render_template('login.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Sesión cerrada correctamente', 'info')
    return redirect(url_for('index'))

# ============= DASHBOARD =============

@app.route('/dashboard')
@login_required
def dashboard():
    recent_workouts = Workout.query.filter_by(user_id=current_user.id).order_by(Workout.date.desc()).limit(5).all()
    recent_meals = Meal.query.filter_by(user_id=current_user.id).order_by(Meal.date.desc()).limit(5).all()
    total_exercises = Exercise.query.filter_by(user_id=current_user.id).count()
    total_workouts = Workout.query.filter_by(user_id=current_user.id).count()
    
    # Calcular calorías totales de hoy
    today = datetime.now().date()
    today_meals = Meal.query.filter(
        Meal.user_id == current_user.id,
        db.func.date(Meal.date) == today
    ).all()
    today_calories = sum(meal.calories for meal in today_meals)
    
    return render_template('dashboard.html', 
                         recent_workouts=recent_workouts,
                         recent_meals=recent_meals,
                         total_exercises=total_exercises,
                         total_workouts=total_workouts,
                         today_calories=today_calories)

# ============= EJERCICIOS =============

# ============= EJERCICIOS =============

@app.route('/exercises')
@login_required
def exercises():
    exercises = Exercise.query.filter_by(user_id=current_user.id).order_by(Exercise.created_at.desc()).all()
    form = ExerciseForm()  # Crear el formulario aquí también
    return render_template('exercises.html', exercises=exercises, form=form)

@app.route('/exercises/add', methods=['GET', 'POST'])
@login_required
def add_exercise():
    form = ExerciseForm()
    if form.validate_on_submit():
        exercise = Exercise(
            name=form.name.data,
            description=form.description.data,
            muscle_group=form.muscle_group.data,
            user_id=current_user.id
        )
        db.session.add(exercise)
        db.session.commit()
        flash('Ejercicio añadido correctamente', 'success')
        return redirect(url_for('exercises'))

    # Si hay errores de validación, mostrar el formulario con errores
    exercises = Exercise.query.filter_by(user_id=current_user.id).all()
    return render_template('exercises.html', form=form, exercises=exercises)

@app.route('/exercises/delete/<int:id>')
@login_required
def delete_exercise(id):
    exercise = Exercise.query.get_or_404(id)
    if exercise.user_id != current_user.id:
        flash('No tienes permiso para eliminar este ejercicio', 'error')
        return redirect(url_for('exercises'))
    db.session.delete(exercise)
    db.session.commit()
    flash('Ejercicio eliminado correctamente', 'success')
    return redirect(url_for('exercises'))

# ============= ENTRENAMIENTOS =============

@app.route('/workouts')
@login_required
def workouts():
    workouts = Workout.query.filter_by(user_id=current_user.id).order_by(Workout.date.desc()).all()
    return render_template('workouts.html', workouts=workouts)

@app.route('/workouts/add', methods=['GET', 'POST'])
@login_required
def add_workout():
    form = WorkoutForm()
    if form.validate_on_submit():
        workout = Workout(
            name=form.name.data,
            description=form.description.data,
            duration=form.duration.data,
            user_id=current_user.id
        )
        db.session.add(workout)
        db.session.commit()
        flash('Entrenamiento añadido. Ahora añade ejercicios!', 'success')
        return redirect(url_for('workout_detail', id=workout.id))
    return render_template('workouts.html', form=form, workouts=Workout.query.filter_by(user_id=current_user.id).all())

@app.route('/workouts/<int:id>')
@login_required
def workout_detail(id):
    workout = Workout.query.get_or_404(id)
    if workout.user_id != current_user.id:
        flash('No tienes permiso para ver este entrenamiento', 'error')
        return redirect(url_for('workouts'))
    
    form = ExerciseEntryForm()
    form.exercise_id.choices = [(e.id, e.name) for e in Exercise.query.filter_by(user_id=current_user.id).all()]
    
    return render_template('workouts.html', workout=workout, form=form, workouts=Workout.query.filter_by(user_id=current_user.id).all())

@app.route('/workouts/<int:id>/add_exercise', methods=['POST'])
@login_required
def add_exercise_to_workout(id):
    workout = Workout.query.get_or_404(id)
    if workout.user_id != current_user.id:
        flash('No tienes permiso para modificar este entrenamiento', 'error')
        return redirect(url_for('workouts'))
    
    form = ExerciseEntryForm()
    form.exercise_id.choices = [(e.id, e.name) for e in Exercise.query.filter_by(user_id=current_user.id).all()]
    
    if form.validate_on_submit():
        entry = ExerciseEntry(
            exercise_id=form.exercise_id.data,
            sets=form.sets.data,
            reps=form.reps.data,
            weight=form.weight.data,
            notes=form.notes.data
        )
        db.session.add(entry)
        workout.exercise_entries.append(entry)
        db.session.commit()
        flash('Ejercicio añadido al entrenamiento', 'success')
    
    return redirect(url_for('workout_detail', id=id))

@app.route('/workouts/delete/<int:id>')
@login_required
def delete_workout(id):
    workout = Workout.query.get_or_404(id)
    if workout.user_id != current_user.id:
        flash('No tienes permiso para eliminar este entrenamiento', 'error')
        return redirect(url_for('workouts'))
    db.session.delete(workout)
    db.session.commit()
    flash('Entrenamiento eliminado correctamente', 'success')
    return redirect(url_for('workouts'))

# ============= RUTINAS =============

@app.route('/routines')
@login_required
def routines():
    routines = Routine.query.filter_by(user_id=current_user.id).order_by(Routine.created_at.desc()).all()
    return render_template('routines.html', routines=routines)

@app.route('/routines/add', methods=['GET', 'POST'])
@login_required
def add_routine():
    form = RoutineForm()
    if form.validate_on_submit():
        routine = Routine(
            name=form.name.data,
            description=form.description.data,
            user_id=current_user.id
        )
        db.session.add(routine)
        db.session.commit()
        flash('Rutina creada correctamente', 'success')
        return redirect(url_for('routines'))
    return render_template('routines.html', form=form, routines=Routine.query.filter_by(user_id=current_user.id).all())

@app.route('/routines/delete/<int:id>')
@login_required
def delete_routine(id):
    routine = Routine.query.get_or_404(id)
    if routine.user_id != current_user.id:
        flash('No tienes permiso para eliminar esta rutina', 'error')
        return redirect(url_for('routines'))
    db.session.delete(routine)
    db.session.commit()
    flash('Rutina eliminada correctamente', 'success')
    return redirect(url_for('routines'))

# ============= COMIDAS =============

@app.route('/meals')
@login_required
def meals():
    meals = Meal.query.filter_by(user_id=current_user.id).order_by(Meal.date.desc()).all()
    
    # Calcular calorías totales de hoy
    today = datetime.now().date()
    today_meals = Meal.query.filter(
        Meal.user_id == current_user.id,
        db.func.date(Meal.date) == today
    ).all()
    today_calories = sum(meal.calories for meal in today_meals)
    today_proteins = sum(meal.proteins for meal in today_meals)
    today_carbs = sum(meal.carbs for meal in today_meals)
    today_fats = sum(meal.fats for meal in today_meals)
    
    return render_template('meals.html', meals=meals, today_calories=today_calories,
                         today_proteins=today_proteins, today_carbs=today_carbs, today_fats=today_fats)

@app.route('/meals/add', methods=['GET', 'POST'])
@login_required
def add_meal():
    form = MealForm()
    if form.validate_on_submit():
        meal = Meal(
            name=form.name.data,
            description=form.description.data,
            calories=form.calories.data,
            proteins=form.proteins.data or 0,
            carbs=form.carbs.data or 0,
            fats=form.fats.data or 0,
            meal_time=form.meal_time.data,
            user_id=current_user.id
        )
        db.session.add(meal)
        db.session.commit()
        flash('Comida añadida correctamente', 'success')
        return redirect(url_for('meals'))
    
    meals = Meal.query.filter_by(user_id=current_user.id).order_by(Meal.date.desc()).all()
    today = datetime.now().date()
    today_meals = Meal.query.filter(
        Meal.user_id == current_user.id,
        db.func.date(Meal.date) == today
    ).all()
    today_calories = sum(meal.calories for meal in today_meals)
    today_proteins = sum(meal.proteins for meal in today_meals)
    today_carbs = sum(meal.carbs for meal in today_meals)
    today_fats = sum(meal.fats for meal in today_meals)
    
    return render_template('meals.html', form=form, meals=meals, today_calories=today_calories,
                         today_proteins=today_proteins, today_carbs=today_carbs, today_fats=today_fats)

@app.route('/meals/delete/<int:id>')
@login_required
def delete_meal(id):
    meal = Meal.query.get_or_404(id)
    if meal.user_id != current_user.id:
        flash('No tienes permiso para eliminar esta comida', 'error')
        return redirect(url_for('meals'))
    db.session.delete(meal)
    db.session.commit()
    flash('Comida eliminada correctamente', 'success')
    return redirect(url_for('meals'))

# ============= DIETAS =============

@app.route('/diets')
@login_required
def diets():
    diets = Diet.query.filter_by(user_id=current_user.id).order_by(Diet.start_date.desc()).all()
    return render_template('diets.html', diets=diets)

@app.route('/diets/add', methods=['GET', 'POST'])
@login_required
def add_diet():
    form = DietForm()
    if form.validate_on_submit():
        diet = Diet(
            name=form.name.data,
            description=form.description.data,
            target_calories=form.target_calories.data,
            user_id=current_user.id
        )
        db.session.add(diet)
        db.session.commit()
        flash('Dieta creada correctamente', 'success')
        return redirect(url_for('diets'))
    return render_template('diets.html', form=form, diets=Diet.query.filter_by(user_id=current_user.id).all())

@app.route('/diets/delete/<int:id>')
@login_required
def delete_diet(id):
    diet = Diet.query.get_or_404(id)
    if diet.user_id != current_user.id:
        flash('No tienes permiso para eliminar esta dieta', 'error')
        return redirect(url_for('diets'))
    db.session.delete(diet)
    db.session.commit()
    flash('Dieta eliminada correctamente', 'success')
    return redirect(url_for('diets'))

# ============= AMIGOS =============

@app.route('/friends')
@login_required
def friends():
    form = FriendCodeForm()
    my_friends = current_user.friends.all()
    return render_template('friends.html', friends=my_friends, form=form, friend_code=current_user.friend_code)

@app.route('/friends/add', methods=['POST'])
@login_required
def add_friend():
    form = FriendCodeForm()
    if form.validate_on_submit():
        friend = User.query.filter_by(friend_code=form.friend_code.data).first()
        if not friend:
            flash('Código de amigo no encontrado', 'error')
        elif friend.id == current_user.id:
            flash('No puedes añadirte a ti mismo', 'error')
        elif current_user.is_friend(friend):
            flash('Ya sois amigos', 'info')
        else:
            current_user.add_friend(friend)
            db.session.commit()
            flash(f'¡{friend.username} añadido como amigo!', 'success')
    return redirect(url_for('friends'))

@app.route('/friends/remove/<int:id>')
@login_required
def remove_friend(id):
    friend = User.query.get_or_404(id)
    if current_user.is_friend(friend):
        current_user.remove_friend(friend)
        db.session.commit()
        flash('Amigo eliminado', 'info')
    return redirect(url_for('friends'))

@app.route('/friends/<int:id>/profile')
@login_required
def friend_profile(id):
    friend = User.query.get_or_404(id)
    if not current_user.is_friend(friend):
        flash('No tienes permiso para ver este perfil', 'error')
        return redirect(url_for('friends'))
    
    friend_workouts = Workout.query.filter_by(user_id=friend.id).order_by(Workout.date.desc()).limit(10).all()
    friend_meals = Meal.query.filter_by(user_id=friend.id).order_by(Meal.date.desc()).limit(10).all()
    friend_routines = Routine.query.filter_by(user_id=friend.id).all()
    friend_diets = Diet.query.filter_by(user_id=friend.id).all()
    
    return render_template('profile.html', user=friend, workouts=friend_workouts, 
                         meals=friend_meals, routines=friend_routines, diets=friend_diets)

if __name__ == '__main__':
    app.run(debug=True)